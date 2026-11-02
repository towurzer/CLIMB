"""
Offline ingest pipeline.

Each stage is an independent module that finds its work by asking the database what is missing,
so stages can run on different machines at the same time.
"""
