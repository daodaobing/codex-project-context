"""Context Pack：纯结果聚合，不做任何业务逻辑。

- 内部只调用: V2 get_project_context + recommend_skills
- 不重新评分、不做预算/snapshot/memory/knowledge/decision/lifecycle
- 返回结构固定: {pack_version, task, project_context, skill_context}
"""

from __future__ import annotations

from server import get_project_context as _v2_project_context
from skills.skill_recommender import recommend as _recommend_skills


def build(project_path: str, task: str) -> dict:
    project_context = _v2_project_context(project_path, task)
    skill_context = _recommend_skills(task)
    return {
        "pack_version": 1,
        "task": task,
        "project_context": project_context,
        "skill_context": skill_context,
    }
