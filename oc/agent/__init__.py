"""Agent jobs that supervise the OC pipeline: the pipeline does the work and owns the gates, the agent handles
judgment and exceptions, and a person approves any store write.

titles.py   drafts NEW titles, colour words and colour family inside `draft.build`, before the title de-dupe;
            every value goes through the same gates as a deterministic one.
explain.py  explains a refused draft or finalize in plain English; it never changes a check or an output.

Both are on only when ai.api_key() returns a key (common.connect is the one gate). With no key nothing is sent
and the draft and finalize are exactly what they are without these jobs.
"""
