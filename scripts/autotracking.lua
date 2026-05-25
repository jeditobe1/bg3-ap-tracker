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
    "equipment_common", "equipment_uncommon", "equipment_rare", "equipment_very_rare",
}

local function code_for_item(item_id)
    if AP_ITEM_ID_TO_CODE[item_id] then return AP_ITEM_ID_TO_CODE[item_id], "toggle" end
    if item_id == 1 then return "level_fragment", "consumable" end
    if item_id >= 5 and item_id <= 34 then return "stat_boost", "consumable" end
    if item_id >= 1000 and item_id < 5000 then
        -- Equipment: routed to per-rarity counter via the generated table.
        local code = AP_EQUIPMENT_ID_TO_RARITY_CODE and AP_EQUIPMENT_ID_TO_RARITY_CODE[item_id]
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

local function apply_status_badges()
    local goal_item = Tracker:FindObjectForCode("goal")
    if goal_item and SLOT_DATA.goal ~= nil then
        goal_item.CurrentStage = SLOT_DATA.goal
    end
    local ks = Tracker:FindObjectForCode("killsanity")
    if ks then ks.Active = (SLOT_DATA.killsanity or 0) ~= 0 end
    local qs = Tracker:FindObjectForCode("questsanity")
    if qs then qs.Active = (SLOT_DATA.questsanity or 0) ~= 0 end
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
    -- UDF goal-progress toggle: if this AP location corresponds to one of the
    -- 16 User Defined Fights, flip its toggle item on so the items-grid row
    -- shows progression. AP_UDF_LOCATION_ID_TO_TOGGLE is emitted by the
    -- generator from options.py UserDefinedFights.valid_keys.
    local udf_code = AP_UDF_LOCATION_ID_TO_TOGGLE and AP_UDF_LOCATION_ID_TO_TOGGLE[location_id]
    if udf_code then
        local t = Tracker:FindObjectForCode(udf_code)
        if t then t.Active = true end
    end
end

local function on_clear(slot_data)
    Tracker.BulkUpdate = true
    SLOT_DATA = slot_data or {}
    reset_all_items()
    local cleared = reconcile_sections()
    apply_status_badges()
    print(string.format("BG3 AP: connected. %d locations already cleared. goal=%s ks=%s qs=%s",
        cleared,
        tostring(SLOT_DATA.goal), tostring(SLOT_DATA.killsanity), tostring(SLOT_DATA.questsanity)))
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
ScriptHost:AddWatchForCode("bg3_lock_goal",        "goal",        lock_setting_if_ap)
ScriptHost:AddWatchForCode("bg3_lock_killsanity",  "killsanity",  lock_setting_if_ap)
ScriptHost:AddWatchForCode("bg3_lock_questsanity", "questsanity", lock_setting_if_ap)

print("BG3 AP autotracker loaded")
