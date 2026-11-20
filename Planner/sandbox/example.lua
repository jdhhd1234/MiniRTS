
-- MinRTS 샌드박스 예제: 이세계 초능력군
-- 초능력으로 비행, 방어, 정신 공격, 재생을 수행하는 군대

-- 1. 염동력 비행대대
local psychic_flyer = {
  name = "염동력비행대대",
  cls = "air",
  atk = 40, dfn = 20, spd = 3.5, rng = 30,
  hp = 220, cost = 120,
  fn = "UCVRA-",

  -- 염동력 비행: 모든 지형을 자유롭게 이동
  move = {
    sea = 1.0,
    mountain = 1.0,
    highland = 1.0,
    swamp = 1.0,
    river = 1.0,
    forest = 1.0
  },

  -- 초능력 폭발: 숲과 도시의 적에게 추가 피해
  on_attack = function(ctx)
    if ctx.target == "unit" and
       (ctx.terrain == "forest" or ctx.terrain == "city") then
      return ctx.damage * 1.3
    end
  end,
}

-- 2. 사이오닉 특수대대
local psionic = {
  name = "사이오닉특수대대",
  cls = "magic",
  -- 기존 magic 병과를 초능력 병과로 재해석
  atk = 18, dfn = 6, spd = 1.4, rng = 50,
  hp = 70, cost = 60,
  fn = "UCF---",

  -- 염동력 보호막: 체력 절반 미만 시 피해 40% 감소
  on_damaged = function(ctx)
    if ctx.hp < ctx.mhp * 0.5 then
      return ctx.damage * 0.6
    end
  end,

  -- 초재생: 비교전 시 초당 최대 체력 2% 회복
  on_tick = function(ctx)
    if not ctx.engaged then
      return 0.02
    end
  end,
}

-- 3. 초능력 강습대대
local psychic_assault = {
  name = "초능력강습대대",
  cls = "inf",
  atk = 28, dfn = 14, spd = 2.0, rng = 35,
  hp = 130, cost = 85,
  fn = "UCF---",

  -- 정신 충격파: 기본 공격 피해 20% 증가
  on_attack = function(ctx)
    if ctx.target == "unit" then
      return ctx.damage * 1.2
    end
  end,
}

-- 4. 초능력 방어대대
local psychic_guard = {
  name = "초능력방어대대",
  cls = "inf",
  atk = 14, dfn = 28, spd = 1.2, rng = 25,
  hp = 180, cost = 90,
  fn = "UCF---",

  -- 공간 왜곡 방벽: 받는 피해 25% 감소
  on_damaged = function(ctx)
    return ctx.damage * 0.75
  end,
}

return {
  battalions = {
    psychic_flyer = psychic_flyer,
    psionic = psionic,
    psychic_assault = psychic_assault,
    psychic_guard = psychic_guard,
  },

  templates = {
    {
      name = "염동력비행사단",
      battalions = {
        "psychic_flyer",
        "psychic_flyer"
      }
    },

    {
      name = "초능력기동사단",
      battalions = {
        "psychic_flyer",
        "mech"
      }
    },

    {
      name = "사이오닉전투사단",
      battalions = {
        "psionic",
        "psionic",
        "psychic_assault",
        "psychic_guard"
      }
    },

    {
      name = "초능력강습사단",
      battalions = {
        "psychic_assault",
        "psychic_assault",
        "psychic_guard",
        "psionic"
      }
    },

    {
      name = "초능력방어사단",
      battalions = {
        "psychic_guard",
        "psychic_guard",
        "psionic",
        "inf"
      }
    },
  },

  enemy_templates = {
    {
      name = "적 기갑사단",
      battalions = {
        "tank", "tank", "mech", "aa"
      }
    },

    {
      name = "적 보병사단",
      battalions = {
        "inf", "inf", "at", "arty"
      }
    },
  },
}
