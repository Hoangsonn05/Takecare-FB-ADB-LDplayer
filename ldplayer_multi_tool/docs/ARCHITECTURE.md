# Architecture

## Components

```text
GUI / CLI
  -> AutomationScheduler
    -> FeedRunner per LDPlayer index
      -> LDConsoleManager
      -> ADBDevice
      -> CommandRunner
  -> RunDatabase
```

## Worker lifecycle

```text
IDLE -> STARTING -> LAUNCHING -> CONNECTING -> OPENING_APP -> RUNNING
RUNNING -> PAUSED -> RUNNING
RUNNING -> STOPPING -> STOPPED
RUNNING -> DONE
* -> ERROR
```

## Threading model

- GUI runs on Qt main thread.
- Device workers run inside `ThreadPoolExecutor`.
- Workers never mutate GUI state directly.
- Workers emit `AutomationEvent` into a `Queue`.
- GUI drains queue with `QTimer` and updates table/log.

## Storage model

`RunDatabase` writes:

- run session metadata to `runs`
- per-device counters to `device_runs`
- event stream to `events`

Storage exceptions are swallowed inside scheduler so database failures do not kill active workers.
