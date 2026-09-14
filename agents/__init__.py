"""Agentic pipeline package.

Roles:
    curriculum_ingestor  - The Ingestor (Oak National Academy retrieval / RAG)
    assessor_agent       - The Assessor (raw demo notes -> structured JSON)
    curriculum_agent     - The Curriculum Mapper (UK data + pupil -> strategy)
    explainer_agent      - The Explainer (child-friendly Markdown)
    problem_setter       - The Problem Setter (70% baseline / 30% targeted)
    crew                 - sequential orchestration (direct + CrewAI)
"""
