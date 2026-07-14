"""Shared SQLAlchemy declarative base.

`agent/trace.py` and `compliance/audit_log.py` each used to declare their
own `class Base(DeclarativeBase): pass`, giving them separate metadata
objects -- `api/rest.py` had to call `create_all()` on both explicitly.
One shared `Base` here means one `create_all()` call covers every ORM
model in the project, and any future store (feedback, rule approvals --
see agent/continuous_learning.py, agent/rule_validation.py) can join the
same metadata by importing this instead of declaring its own.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
