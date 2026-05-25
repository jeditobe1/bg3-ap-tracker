-- BG3 AP
-- init.lua

Tracker:AddItems("items/items.json")
Tracker:AddMaps("maps/maps.json")
Tracker:AddLocations("locations/locations.json")
Tracker:AddLayouts("layouts/regions.json")
Tracker:AddLayouts("layouts/standard.json")

if Archipelago then
    ScriptHost:LoadScript("scripts/autotracking_generated.lua")
    ScriptHost:LoadScript("scripts/autotracking.lua")
end
