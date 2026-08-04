"""
Offline ingest pipeline.

Each stage is an independent module driven by the ingest_jobs state machine, so stages can run
on different machines at the same time.
"""
