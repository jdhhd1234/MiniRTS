-- MinRTS 샌드박스 예제: 드래곤 대대와 마법사 대대
-- 형식은 Planner/sandbox/README.md 참고.

local dragon = {
  name = "드래곤대대",
  cls = "air",              -- 상성: 대공(aa)에게 2배 피해를 받는다
  atk = 40, dfn = 20, spd = 3.5, rng = 30, hp = 220, cost = 120,
  fn = "UCVRA-",
  move = { sea = 1.0, mountain = 1.0, highland = 1.0, swamp = 1.0, river = 1.0, forest = 1.0 },

  -- 불길: 숲·도시에 있는 적에게 피해 +30%
  on_attack = function(ctx)
    if ctx.target == "unit" and (ctx.terrain == "forest" or ctx.terrain == "city") then
      return ctx.damage * 1.3
    end
  end,
}

local mage = {
  name = "마법사대대",
  cls = "magic",            -- 상성표에 없는 병과: 모든 상성 1.0
  atk = 18, dfn = 6, spd = 1.4, rng = 50, hp = 70, cost = 60,
  fn = "UCF---",

  -- 보호막: 체력이 절반 이하면 받는 피해 -40%
  on_damaged = function(ctx)
    if ctx.hp < ctx.mhp * 0.5 then
      return ctx.damage * 0.6
    end
  end,

  -- 치유술: 교전 중이 아닐 때 초당 최대 체력 2% 회복
  on_tick = function(ctx)
    if not ctx.engaged then
      return 0.02
    end
  end,
}

return {
  battalions = { dragon = dragon, mage = mage },

  templates = {
    { name = "비룡사단",   battalions = { "dragon", "dragon" } },          -- 바다를 건넌다
    { name = "용기병사단", battalions = { "dragon", "mech" } },            -- 기계화가 있어 바다는 못 건넌다
    { name = "마도사단",   battalions = { "mage", "mage", "inf", "inf" } },
    { name = "보병사단",   battalions = { "inf", "inf", "arty", "eng" } },
  },

  enemy_templates = {
    { name = "적 기갑", battalions = { "tank", "tank", "mech", "aa" } },
    { name = "적 보병", battalions = { "inf", "inf", "at", "arty" } },
  },
}
