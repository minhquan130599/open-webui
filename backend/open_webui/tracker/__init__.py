"""Project tracker: keeps a per-channel project state up to date from channel activity.

Each channel is treated as one project. New channel messages mark the channel dirty; a background
worker folds the unprocessed messages into a structured state with one LLM call per batch. When a
model is mentioned in the channel, the stored state plus the not-yet-processed tail of messages is
injected into its prompt, so answers are current without re-reading the channel history.
"""
