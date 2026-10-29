# Firmware review compatibility reference

The current factory watcher instructions are [CONTROLLER.md](../CONTROLLER.md).
The completion policy is [REVIEW.md](REVIEW.md), using `algorithm-library-v2`.

This file is retained for old documentation links. It does not configure a
separate Night Watch, watched-thread list, timer, retry policy or completion gate.
Do not create or enable legacy schedules from this reference.

Use the registered owner and saved factory jobs. Never resume periodic messages
or contact workers merely because this file was opened.
