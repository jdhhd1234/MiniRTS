"""HTN (Hierarchical Task Network): 복합 작업(Compound)을 조건에 맞는 방법(Method)으로
쪼개 실행 가능한 기본 작업(Primitive) 목록으로 만든다.

방법은 적힌 순서대로 검사해 조건이 처음 맞는 것을 쓴다 (앞쪽이 우선순위가 높다).
"""

from dataclasses import dataclass


@dataclass
class Primitive:
    name: str
    operator: object       # callable(context) -> None : 실제 게임 명령


@dataclass
class Method:
    name: str
    condition: object      # callable(context) -> bool
    subtasks: list         # 작업 이름 목록


@dataclass
class Compound:
    name: str
    methods: list


class Domain:
    def __init__(self, tasks):
        self.tasks = {}

        for task in tasks:
            self.tasks[task.name] = task

    def decompose(self, root, context, max_tasks=32):
        """root 작업을 기본 작업 목록으로 분해. 맞는 방법이 없으면 None.

        context.trace에 고른 방법을 남긴다 (디버깅용).
        """

        result = []
        stack = [root]

        while stack:
            name = stack.pop()
            task = self.tasks[name]

            if isinstance(task, Primitive):
                result.append(task)

                if len(result) > max_tasks:
                    raise RuntimeError(f"HTN 분해가 너무 깁니다: {root}")
                continue


            # 복합 작업: 조건이 맞는 첫 방법을 고른다.
            chosen = None

            for method in task.methods:
                if method.condition(context):
                    chosen = method
                    break

            if chosen is None:
                return None

            context.trace.append(f"{task.name}:{chosen.name}")

            # 스택이라 뒤에서부터 넣어야 앞 작업부터 실행된다.
            for subtask in reversed(chosen.subtasks):
                stack.append(subtask)

        return result
