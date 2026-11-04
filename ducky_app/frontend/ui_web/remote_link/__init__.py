"""Remote control link on the PC: presence beats, request dedupe and the input lane.

- ``presence``: when to report this PC online, on its own thread, and when a
  viewer may call it gone (only after 3 missed beats).
- ``requests``: remote requests carry an id; a retry after a dropped link
  replays the first answer instead of running the call twice.
- ``input_lane``: remote input and control frames run on their own small
  workers, never behind file or chat traffic.
"""
