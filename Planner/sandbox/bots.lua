-- MinRTS 샌드박스 예제: 봇전투 (용군단 대 마법군)
-- 로비 > 샌드박스 > "봇전투 (AI 대 AI)"를 켜고 출격하면 양쪽 AI가 싸우고 플레이어는 관전한다.
-- 적군(enemy)도 Lua 대대·편제·시작 사단·AI 성향을 쓸 수 있다.

local dragon = {
  name = "드래곤대대", cls = "air",
  atk = 40, dfn = 20, spd = 3.5, rng = 30, hp = 220, cost = 120,
  fn = "UCVRA-",
  move = { sea = 1.0, mountain = 1.0, highland = 1.0, swamp = 1.0, river = 1.0, forest = 1.0 },

  -- 화염: 숲·도시의 적에게 피해 +30%
  on_attack = function(ctx)
    if ctx.target == "unit" and (ctx.terrain == "forest" or ctx.terrain == "city") then
      return ctx.damage * 1.3
    end
  end,
}

local mage = {
  name = "마법사대대", cls = "magic",
  atk = 18, dfn = 6, spd = 1.4, rng = 50, hp = 70, cost = 60,
  fn = "UCF---",

  -- 보호막: 체력 절반 이하에서 받는 피해 -40%
  on_damaged = function(ctx)
    if ctx.hp < ctx.mhp * 0.5 then
      return ctx.damage * 0.6
    end
  end,

  -- 치유술: 비교전 시 초당 최대 체력 2% 회복
  on_tick = function(ctx)
    if not ctx.engaged then
      return 0.02
    end
  end,
}

return {
  battalions = { dragon = dragon, mage = mage },

  -- 청군 (player): 용군단
  templates = {
    { name = "비룡사단",   battalions = { "dragon", "dragon" } },
    { name = "용기병사단", battalions = { "dragon", "tank", "mech" } },
  },
  player_start = { "비룡사단", "용기병사단", "용기병사단" },

  -- 적군 (enemy): 마법군. 적 편제에도 Lua 대대를 넣을 수 있다.
  enemy_templates = {
    { name = "마도사단",   battalions = { "mage", "mage", "inf", "aa" } },
    { name = "마법보병사단", battalions = { "mage", "inf", "inf", "at" } },
  },
  enemy_start = 4,          -- 숫자면 편제를 돌아가며 그 수만큼

  -- AI 성향 (Utility 가중치, 기본 1.0). caution: 공격 전에 모을 전력 배수
  ai = {
    player = { attack = 1.4, defend = 0.8, caution = 0.8 },   -- 공격적
    enemy  = { attack = 0.8, defend = 1.5, caution = 1.5 },   -- 수비적
  },
}
