-- BG3 AP autotracker (hand-authored)
-- Reads globals defined by scripts/autotracking_generated.lua:
--   AP_ITEM_ID_TO_CODE        (id -> toggle code)
--   AP_LOCATION_ID_TO_SECTION (id -> "@Region/Location/Location" code)
--   REGION_SECTION_CODES      (array of all section codes)

-- Visibility is driven by the Goal / Killsanity / Questsanity items (see
-- items.json + locations.json visibility_rules). on_clear sets those from
-- slot_data so AP-connected behavior matches what the server's pool implies;
-- in non-AP mode the user clicks the same items to filter manually.

SLOT_DATA = {}

local DISTINCT_CONSUMABLE_CODES = {
    "level_fragment", "stat_boost", "filler", "trap",
    "equipment_pre_halsin", "equipment_act1", "equipment_act2", "equipment_act3",
    "gate_progressive_moonlight_towers",
}

local function code_for_item(item_id)
    if AP_ITEM_ID_TO_CODE[item_id] then return AP_ITEM_ID_TO_CODE[item_id], "toggle" end
    if item_id == 1 then return "level_fragment", "consumable" end
    if item_id >= 5 and item_id <= 34 then return "stat_boost", "consumable" end
    -- Region-locking Progressive Moonlight Towers (apworld v0.6.0+, AP id
    -- 114). Receivable up to 5 times: 4 unlocks Moonrise, 5 unlocks the
    -- Mindflayer Colony. Single-receive gates 100..113 are routed via
    -- AP_ITEM_ID_TO_CODE; 114 is the only counter and lands here.
    if item_id == 114 then return "gate_progressive_moonlight_towers", "consumable" end
    if item_id >= 1000 and item_id < 5000 then
        -- Equipment: routed to per-act-gate counter via the generated table.
        local code = AP_EQUIPMENT_ID_TO_ACT_GATE_CODE and AP_EQUIPMENT_ID_TO_ACT_GATE_CODE[item_id]
        if code then return code, "consumable" end
        return nil, nil
    end
    if item_id >= 5000 and item_id < 7000 then return "filler", "consumable" end
    if item_id >= 7000 and item_id < 8000 then return "trap", "consumable" end
    return nil, nil
end

local function reset_all_items()
    for _, code in pairs(AP_ITEM_ID_TO_CODE) do
        local o = Tracker:FindObjectForCode(code)
        if o then o.Active = false end
    end
    for _, code in ipairs(DISTINCT_CONSUMABLE_CODES) do
        local o = Tracker:FindObjectForCode(code)
        if o then o.AcquiredCount = 0 end
    end
    if AP_UDF_LOCATION_ID_TO_TOGGLE then
        for _, code in pairs(AP_UDF_LOCATION_ID_TO_TOGGLE) do
            local o = Tracker:FindObjectForCode(code)
            if o then o.Active = false end
        end
    end
end

-- Goal stage codes from items.json Goal progressive (matches the values in
-- apworld options.py: 0=Halsin, 1=Wwargaz, 2=Act1UDF, 3=Myrkul, 4=Act2UDF).
local GOAL_HALSIN, GOAL_WWARGAZ, GOAL_ACT1_UDF, GOAL_MYRKUL, GOAL_ACT2_UDF = 0, 1, 2, 3, 4

-- Region-locking gate items (apworld v0.6.0+). When BlockEntrances is on,
-- the items in this player's pool depend on goal -- the rest are auto-enabled
-- so access_rules referencing them still pass. Mirrors items.py:159-181.
local ALL_GATE_TOGGLES = {
    "gate_nautiloid_control_panel", "gate_withers_crypt",
    "gate_blighted_village_well", "gate_goblin_camp",
    "gate_underdark", "gate_hags_fireplace", "gate_zhentarim_basement",
    "gate_grymforge", "gate_mountain_pass", "gate_creche",
    "gate_act2", "gate_last_light_basement", "gate_reithwins_masons_guild",
    "gate_shar_trials",
}
local ALL_GATE_COUNTERS = { "gate_progressive_moonlight_towers" }

local function pool_gates_for_goal(goal)
    -- Return the set of gate codes that ARE in the player's pool for this
    -- goal when BlockEntrances is on. Codes outside this set get
    -- auto-enabled so they don't block access_rules pointlessly.
    local pool = {
        gate_nautiloid_control_panel = true,
        gate_withers_crypt = true,
        gate_goblin_camp = true,
    }
    if goal == GOAL_HALSIN then
        pool.gate_blighted_village_well = true
    else
        pool.gate_underdark = true
        pool.gate_hags_fireplace = true
        pool.gate_zhentarim_basement = true
        pool.gate_grymforge = true
        pool.gate_mountain_pass = true
        pool.gate_creche = true
        if goal ~= GOAL_WWARGAZ and goal ~= GOAL_ACT1_UDF then
            pool.gate_act2 = true
            pool.gate_last_light_basement = true
            pool.gate_reithwins_masons_guild = true
            pool.gate_shar_trials = true
            pool.gate_progressive_moonlight_towers = true  -- counter, max 5
        end
    end
    return pool
end

local function apply_gate_auto_enable()
    -- When BlockEntrances is off, every gate is "given" so access_rules
    -- referencing them pass trivially. When BlockEntrances is on, gates
    -- outside the player's goal-pool get the same treatment (they'd never
    -- arrive otherwise). Gates inside the pool are left to natural item
    -- receipt via on_item.
    --
    -- Visual hint: auto-enabled (out-of-pool) gates get IconMods="@disabled"
    -- so they render greyed -- the user can tell at a glance that those
    -- aren't actually receivable items but virtual bypasses for access
    -- rule purposes. In-pool gates clear IconMods so they show in their
    -- normal bright-on / dim-off styling.
    local block_on = (SLOT_DATA.block_entrances or 0) ~= 0
    local goal = SLOT_DATA.goal or 0
    local pool = block_on and pool_gates_for_goal(goal) or {}
    for _, code in ipairs(ALL_GATE_TOGGLES) do
        local t = Tracker:FindObjectForCode(code)
        if t then
            if not pool[code] then
                t.Active = true
                t.IconMods = "@disabled"
            else
                t.IconMods = ""
            end
        end
    end
    for _, code in ipairs(ALL_GATE_COUNTERS) do
        local c = Tracker:FindObjectForCode(code)
        if c then
            if not pool[code] then
                c.AcquiredCount = 5
                c.IconMods = "@disabled"
            else
                c.IconMods = ""
            end
        end
    end
end

local function apply_status_badges()
    local goal_item = Tracker:FindObjectForCode("goal")
    if goal_item and SLOT_DATA.goal ~= nil then
        goal_item.CurrentStage = SLOT_DATA.goal
    end
    local ks = Tracker:FindObjectForCode("killsanity")
    if ks then ks.Active = (SLOT_DATA.killsanity or 0) ~= 0 end
    local qs = Tracker:FindObjectForCode("questsanity")
    if qs then qs.Active = (SLOT_DATA.questsanity or 0) ~= 0 end
    local be = Tracker:FindObjectForCode("block_entrances")
    if be then be.Active = (SLOT_DATA.block_entrances or 0) ~= 0 end
    apply_gate_auto_enable()
end

local function reconcile_sections()
    -- Reset every known section to uncleared, then mark checked ones cleared.
    -- Visibility filtering happens declaratively via visibility_rules
    -- referencing the goal/killsanity/questsanity items.
    local checked = {}
    if Archipelago.CheckedLocations then
        for _, lid in ipairs(Archipelago.CheckedLocations) do checked[lid] = true end
    end
    local cleared_count = 0
    for lid, section_code in pairs(AP_LOCATION_ID_TO_SECTION) do
        local s = Tracker:FindObjectForCode(section_code)
        if s then
            if checked[lid] then
                s.AvailableChestCount = 0
                cleared_count = cleared_count + 1
            else
                s.AvailableChestCount = s.ChestCount
            end
        end
    end
    -- Mirror UDF progress on the items-grid toggle row.
    if AP_UDF_LOCATION_ID_TO_TOGGLE then
        for lid, code in pairs(AP_UDF_LOCATION_ID_TO_TOGGLE) do
            local t = Tracker:FindObjectForCode(code)
            if t then t.Active = checked[lid] == true end
        end
    end
    return cleared_count
end

local function mark_location_cleared(location_id)
    local section_code = AP_LOCATION_ID_TO_SECTION[location_id]
    if section_code then
        local s = Tracker:FindObjectForCode(section_code)
        if s then s.AvailableChestCount = 0 end
    end
    -- Goal-progress toggle: if this AP location corresponds to one of the
    -- 16 User Defined Fights (or the Halsin rescue), flip its toggle on so
    -- the items-grid row shows progression. AP_UDF_LOCATION_ID_TO_TOGGLE is
    -- emitted by the generator from UserDefinedFights.valid_keys plus a
    -- hardcoded entry for the Halsin rescue (id 114 -> udf_rescue_halsin).
    local udf_code = AP_UDF_LOCATION_ID_TO_TOGGLE and AP_UDF_LOCATION_ID_TO_TOGGLE[location_id]
    if udf_code then
        local t = Tracker:FindObjectForCode(udf_code)
        if t then t.Active = true end
    end
end

-- "Follow Player Location": when the opt_follow_tab toggle is on, switch the
-- active tab to the region of the most recent *live* check. Region name is the
-- segment of the section code between "@" and the first "/", which matches the
-- layout tab titles ("@Druid Grove/..." -> tab "Druid Grove").
--
-- On connect, PopTracker replays every already-checked location through the
-- location handler. We suppress tab-following for a short settle window after
-- on_clear so that backlog doesn't yank the UI to whatever check happens to be
-- last. Live checks (made while playing) arrive well after the window closes.
local TAB_FOLLOW_SETTLE = 0  -- seconds remaining during which checks won't switch tabs

local function follow_tab_to_location(location_id)
    if TAB_FOLLOW_SETTLE > 0 then return end
    local opt = Tracker:FindObjectForCode("opt_follow_tab")
    if not (opt and opt.Active) then return end
    local section_code = AP_LOCATION_ID_TO_SECTION[location_id]
    if not section_code then return end
    local region = section_code:match("^@([^/]+)/")
    if not region then return end
    -- Region tabs are nested inside per-act tabs; activate the act first so the
    -- region's tab group is the visible one, then the region itself. Each tab
    -- widget ignores names it doesn't own, so sending both is safe.
    local act = AP_REGION_TO_ACT_TAB and AP_REGION_TO_ACT_TAB[region]
    if act then Tracker:UiHint("ActivateTab", act) end
    Tracker:UiHint("ActivateTab", region)
end

local function on_clear(slot_data)
    Tracker.BulkUpdate = true
    SLOT_DATA = slot_data or {}
    reset_all_items()
    local cleared = reconcile_sections()
    apply_status_badges()
    -- Suppress tab-following while the connect backlog replays. If frame handlers
    -- aren't available (older PopTracker), leave it at 0 so the feature still works.
    if ScriptHost.AddOnFrameHandler then TAB_FOLLOW_SETTLE = 2.0 end
    print(string.format("BG3 AP: connected. %d locations already cleared. goal=%s ks=%s qs=%s be=%s",
        cleared,
        tostring(SLOT_DATA.goal), tostring(SLOT_DATA.killsanity), tostring(SLOT_DATA.questsanity),
        tostring(SLOT_DATA.block_entrances)))
    Tracker.BulkUpdate = false
end

local function on_item(index, item_id, item_name, player_number)
    local code, kind = code_for_item(item_id)
    if not code then
        print(string.format("BG3 AP: unhandled item id=%s name=%s", tostring(item_id), tostring(item_name)))
        return
    end
    local o = Tracker:FindObjectForCode(code)
    if not o then return end
    if kind == "toggle" then
        o.Active = true
    else
        o.AcquiredCount = (o.AcquiredCount or 0) + 1
    end
end

local function on_location(location_id, location_name)
    mark_location_cleared(location_id)
    follow_tab_to_location(location_id)
end

-- Locking: when AP is connected the slot's goal/killsanity/questsanity are
-- authoritative. If the user clicks one of the settings items, revert it to
-- the slot_data value. In non-AP mode the watch is a no-op so users can still
-- configure manually.
local LOCK_RESYNCING = false

local function lock_setting_if_ap(code)
    if LOCK_RESYNCING then return end
    if Archipelago.PlayerNumber and Archipelago.PlayerNumber > 0 then
        LOCK_RESYNCING = true
        apply_status_badges()
        LOCK_RESYNCING = false
    end
end

Archipelago:AddClearHandler("bg3_clear", on_clear)
Archipelago:AddItemHandler("bg3_items", on_item)
Archipelago:AddLocationHandler("bg3_loc", on_location)
ScriptHost:AddWatchForCode("bg3_lock_goal",             "goal",             lock_setting_if_ap)
ScriptHost:AddWatchForCode("bg3_lock_killsanity",       "killsanity",       lock_setting_if_ap)
ScriptHost:AddWatchForCode("bg3_lock_questsanity",      "questsanity",      lock_setting_if_ap)
ScriptHost:AddWatchForCode("bg3_lock_block_entrances",  "block_entrances",  lock_setting_if_ap)
if ScriptHost.AddOnFrameHandler then
    ScriptHost:AddOnFrameHandler("bg3_tab_settle", function(elapsed)
        if TAB_FOLLOW_SETTLE > 0 then TAB_FOLLOW_SETTLE = TAB_FOLLOW_SETTLE - elapsed end
    end)
end

print("BG3 AP autotracker loaded")
