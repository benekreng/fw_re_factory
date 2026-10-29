"""Evidence-based review history; elapsed run time is not CPU time or token cost."""
import datetime as dt

from app import Problem, now

VERSION = 'readability-modifiability-v1'
OUTCOMES = {'resolved', 'partial', 'unresolved', 'not_attempted', 'not_assessed'}
OBSTACLES = {'implementation', 'reference_evidence', 'tolerance',
             'provider_or_infrastructure', 'none', 'unknown'}


def validate(assessment):
    if not isinstance(assessment, dict) or not all(
            isinstance(assessment.get(k), str) and assessment[k].strip()
            for k in ('summary', 'initialDelivery')):
        raise Problem('Worker assessment needs summary and initialDelivery evidence.', 409)
    corrections = assessment.get('corrections')
    if not isinstance(corrections, list):
        raise Problem('Worker assessment corrections must be an array.', 409)
    for item in corrections:
        if (not isinstance(item, dict) or item.get('outcome') not in OUTCOMES or
                item.get('obstacle') not in OBSTACLES or
                not all(isinstance(item.get(k), str) and item[k].strip()
                        for k in ('reviewId', 'explanation')) or
                not evidence(item.get('evidence'))):
            raise Problem('Each correction assessment needs an outcome, obstacle and specific evidence.', 409)
    effort = assessment.get('effort')
    if (not isinstance(effort, dict) or effort.get('conclusion') not in
            {'reasonable', 'potentially_excessive', 'unknown'} or
            not isinstance(effort.get('explanation'), str) or not effort['explanation'].strip() or
            not evidence(effort.get('evidence'), allow_empty=effort.get('conclusion') == 'unknown')):
        raise Problem('Effort assessment needs evidence or an explicit unknown conclusion.', 409)
    if not isinstance(assessment.get('lessons'), list) or not all(
            isinstance(x, str) and x.strip() for x in assessment['lessons']):
        raise Problem('Worker assessment lessons must be an array of nonempty strings.', 409)


def evidence(items, allow_empty=False):
    return (isinstance(items, list) and (bool(items) or allow_empty) and
            all(isinstance(x, str) and x.strip() for x in items))


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def elapsed(start, end):
    a, b = timestamp(start), timestamp(end)
    return round(b - a, 3) if a is not None and b is not None and b >= a else None


def timeline_summary(runs):
    """Deduplicate run IDs. Missing/invalid timestamps remain unknown, never zero."""
    unique = {}
    for run in runs:
        if isinstance(run, dict) and isinstance(run.get('runId'), str):
            unique[run['runId']] = run
    durations, unknown = [], []
    for rid, run in unique.items():
        seconds = elapsed(run.get('startedAt'), run.get('completedAt'))
        if seconds is None:
            unknown.append(rid)
        else:
            durations.append(seconds)
    return {'observedRuns': len(unique), 'timedRuns': len(durations),
            'unknownDurationRunIds': unknown,
            'summedRunElapsedSeconds': round(sum(durations), 3) if durations else None,
            'meaning': 'Sum of observed start-to-completion durations, including tool waits; '
                       'not CPU time, continuous productive work, tokens or subscription usage.'}


def build(job, rounds):
    corrections, assessments, runs = [], [], []
    for index, r in enumerate(rounds):
        timeline = r.get('workerTimeline') or []
        runs.extend(timeline)
        result = r.get('result') or {}
        if result.get('workerAssessment'):
            assessments.append({'reviewId': r['id'], 'criteriaVersion': r.get('criteriaVersion'),
                                'recordedAt': r.get('updatedAt'),
                                'assessment': result['workerAssessment']})
        sent = r.get('followupSent')
        if not sent:
            continue
        run_id = sent.get('runId')
        observed = next((run for later in rounds[index + 1:]
                         for run in later.get('workerTimeline') or []
                         if run.get('runId') == run_id), {})
        next_review = next((later for later in rounds[index + 1:]
                           if later.get('state') == 'completed' and
                           later.get('workerThreadId') == r.get('workerThreadId') and
                           later.get('workerRunId') != r.get('workerRunId')), None)
        corrections.append({'reviewId': r['id'], 'workerThreadId': r.get('workerThreadId'),
            'messageId': sent.get('messageId'), 'workerRunId': run_id,
            'sentAt': r.get('followupSentAt'), 'completedAt': observed.get('completedAt'),
            'responseWallSeconds': elapsed(r.get('followupSentAt'), observed.get('completedAt')),
            'runElapsedSeconds': elapsed(observed.get('startedAt'), observed.get('completedAt')),
            'nextReviewId': next_review['id'] if next_review else None,
            'nextReviewVerdict': (next_review.get('result') or {}).get('verdict') if next_review else None})
    run_map = {r['runId']: r for r in runs if isinstance(r, dict) and isinstance(r.get('runId'), str)}
    observed = [{k: r.get(k) for k in ('runId', 'providerInstanceId', 'model', 'status',
                                      'requestedAt', 'startedAt', 'completedAt')}
                | {'runElapsedSeconds': elapsed(r.get('startedAt'), r.get('completedAt'))}
                for r in run_map.values()]
    return {'schemaVersion': 1, 'jobId': job['id'], 'name': job['name'], 'updatedAt': now(),
            'workerSelection': job['request'].get('selection'),
            'timing': timeline_summary(runs), 'observedRuns': observed, 'corrections': corrections,
            'assessments': assessments,
            'limitations': ['Timeline coverage is limited to supplied T3 reads; missing history is unknown.',
                'Historical correction send timestamps may be unavailable.',
                'Elapsed time alone does not establish waste, infeasibility, or an unsuitable tolerance.',
                'These are observations of different tasks, not a controlled model benchmark.']}


def export(store, job, rounds):
    from controller import atomic_json
    report = build(job, rounds)
    path = store.data / 'jobs' / job['id'] / 'WORKER_PERFORMANCE.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(path, report)
    return report
