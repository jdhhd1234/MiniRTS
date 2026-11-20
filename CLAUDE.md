# Python Coding Style Rules

## 1. 명시적인 반복문 사용

- 리스트 컴프리헨션, 딕셔너리 컴프리헨션, 집합 컴프리헨션을 사용하지 않는다.
- 데이터 변환 및 필터링은 일반적인 `for`문으로 작성한다.
- 조건과 데이터 추가 과정은 별도의 줄로 분리한다.
- 복잡한 표현식보다 명시적이고 추적 가능한 제어 흐름을 우선한다.

## 2. 데이터 구조 설계

- 구조가 고정된 데이터를 표현할 때는 `@dataclass`를 적극적으로 사용한다.
- 서로 다른 의미를 가진 값을 숫자 인덱스로 구분하는 구조는 지양한다.
- `data[0]`, `data[1]`처럼 의미를 알기 어려운 접근 대신 명명된 속성을 사용한다.
- 단, 단순한 시퀀스 순회, 좌표, 배열, 슬라이싱 등 인덱스가 자연스러운 상황에서는 인덱스를 허용한다.
- 직렬화, 외부 API, 기존 프로토콜과의 호환성이 필요할 때는 적절한 변환 계층을 사용한다.

## 3. 함수 작성 방식

- 서로 다른 처리 단계는 빈 줄과 주석으로 명확히 구분한다.
- 복잡한 표현식을 한 줄에 압축하지 않는다.
- 중간 결과에는 의미 있는 변수명을 부여한다.
- 복잡한 반환값은 별도 변수에 구성한 후 반환한다.
- 함수가 너무 많은 책임을 가지면 의미 있는 단위로 분리한다.

## 4. 가독성 및 유지보수성

- 코드 길이를 최소화하는 것보다 읽기 쉬운 구조를 우선한다.
- 디버깅할 때 중간 상태를 쉽게 확인할 수 있도록 작성한다.
- 불필요한 추상화와 과도한 클래스 분리는 피한다.
- 기존 프로젝트의 명명 규칙 및 아키텍처와 일관성을 유지한다.

## 5. 규칙 준수

- 새 코드를 작성할 때뿐 아니라 기존 코드를 수정할 때도 이 규칙을 적용한다.
- 새로운 코드에서 금지된 문법을 사용하지 않는다.
- 기존 코드 전체를 무관하게 재작성하지 않고, 수정 범위 내에서 점진적으로 개선한다.

```py 이거는 별로 안좋은 예시
def state_message(self):
        changes = [[cell, self.owner[cell]] for cell in sorted(self._changed)
                   if self.owner[cell] != self._last_owner[cell]]
        for cell, side in changes:
            self._last_owner[cell] = side
        self._changed.clear()
        weather = None
        if self.weather != self._last_weather:
            weather = self.weather.copy()
            self._last_weather = weather.copy()
        return {"t": "state", "time": self.time, "res": round(self.resources[1], 2),
                "income": round(self.income(1), 4), "build_speed": self.build_speed(1),
                "queue": [{"template": order.template, "name": self.templates[1][order.template]["name"],
                           "progress": round(order.progress, 4)} for order in self.queues[1]],
                "units": [unit.state() for unit in self.units.values()],
                "bases": [base.state() for base in self.bases], "own": changes,
                "weather": weather, "terr": self.territory[1:]}
```

```py 이거는 좋은예시
def state_message(self):

    # 1. 변경된 소유권 정보 처리
    changes = []

    for cell in sorted(self._changed):
        side = self.owner[cell]

        if side != self._last_owner[cell]:
            changes.append([cell, side])

    for cell, side in changes:
        self._last_owner[cell] = side

    self._changed.clear()


    # 2. 날씨 정보 처리
    weather = None

    if self.weather != self._last_weather:
        weather = self.weather.copy()
        self._last_weather = weather.copy()


    # 3. 생산 대기열 정보 처리
    queue = []

    for order in self.queues[1]:
        template = order.template
        name = self.templates[1][template]["name"]
        progress = round(order.progress, 4)

        queue.append({
            "template": template,
            "name": name,
            "progress": progress
        })


    # 4. 유닛 정보 처리
    units = []

    for unit in self.units.values():
        units.append(unit.state())


    # 5. 기지 정보 처리
    bases = []

    for base in self.bases:
        bases.append(base.state())


    # 6. 최종 상태 메시지 생성
    message = {
        "t": "state",
        "time": self.time,
        "res": round(self.resources[1], 2),
        "income": round(self.income(1), 4),
        "build_speed": self.build_speed(1),
        "queue": queue,
        "units": units,
        "bases": bases,
        "own": changes,
        "weather": weather,
        "terr": self.territory[1:]
    }

    return message
```