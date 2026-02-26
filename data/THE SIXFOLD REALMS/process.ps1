## region (.json) = zone (.toml)

$path = "C:\Stuff\mud\data\THE SIXFOLD REALMS"

function get-json($filename) {
    $json = Get-Content "$path\$filename" -Raw
    return ConvertFrom-Json $json
}
function get-file($filename) {
    $lines = Get-Content "$path\$filename"
    return $lines
}

function makeid-fromname($name) {
    write-host $name -ForegroundColor Cyan
    write-host ($name.ToLower() -replace '\s+', '_') -ForegroundColor red
    return ($name.ToLower() -replace '\s+', '_')
}

function quote-and-join($array) {
    return ($array | ForEach-Object { '"{0}"' -f $_ }) -join ", "
}

$rooms = @()
$items = @()
$npcs = @()
$regions = @()
$roomConnections = @()
$region_prefixes = @{
    "riverlands_confluence" = "riverlands_"
    "dragon_peaks"          = "dp_"
    "mistfen_expanse"       = "me_"
    "verdant_heartwood"     = "vh_"
    "gloamreach"            = "gr_"
    "mycelial_plains"       = "mp_"
    "core_chamber"          = "cc_"
}

function get-region-prefix($region) {
    if ($region_prefixes.ContainsKey($region)) {
        return $region_prefixes[$region]
    }
    else {
        return $region + "_"
    }
}

write-host 'Rooms' -ForegroundColor green
foreach ($file in (get-childitem "*_rooms.json")) {
    $region = ""
    $file.Name -match '^region_\d+_(.+?)_rooms\.json$' | Out-Null
    $region = $Matches[1] 
    write-host "Processing room file: $($file.Name)"
    write-host "Region: $region"
    $itemData = (get-json $file.Name).rooms
    foreach ($item in $itemData) {
        $item | Add-Member -MemberType NoteProperty -Name "newid" -Value $(makeid-fromname $item.name)
        $item | Add-Member -MemberType NoteProperty -Name "region_home" -Value $region -Force
        $rooms += $item
    }
}

write-host 'NPCs' -ForegroundColor green
foreach ($file in (get-childitem "*_enemies.json")) {
    $region = ""
    $file.Name -match '^region_\d+_(.+?)_enemies\.json$' | Out-Null
    $region = $Matches[1] 
    write-host "Processing enemy file: $($file.Name)"
    write-host "Region: $region"
    $itemData = (get-json $file.Name).enemies
    foreach ($item in $itemData) {
        $item | Add-Member -MemberType NoteProperty -Name "region_home" -Value $region -Force
        $item | Add-Member -MemberType NoteProperty -Name "friendly" -Value $false -Force
        $item | Add-Member -MemberType NoteProperty -Name "speed" -Value 0 -Force
        $item | Add-Member -MemberType NoteProperty -Name "awareness" -Value 0 -Force
        $npcs += $item
    }
}
$itemData = (get-json "npcs.json").npcs
foreach ($item in $itemData) {
    #$item | Add-Member -MemberType NoteProperty -Name "region_home" -Value $region
    $item | Add-Member -MemberType NoteProperty -Name "hp" -Value 100 -Force
    $item | Add-Member -MemberType NoteProperty -Name "attack" -Value 100  -Force
    $item | Add-Member -MemberType NoteProperty -Name "defense" -Value 100 -Force
    $npcs += $item
}

write-host 'Items' -ForegroundColor green
foreach ($file in (get-childitem "*_items.json" -Exclude "global_items.json")) {
    $region = ""
    $file.Name -match '^region_\d+_(.+?)_items\.json$' | Out-Null
    $region = $Matches[1] 
    write-host "Processing item file: $($file.Name)"
    write-host "Region: $region"
    $itemData = (get-json $file.Name).items
    foreach ($item in $itemData) {
        $item | Add-Member -MemberType NoteProperty -Name "region_home" -Value $region -Force
        $items += $item
    }
}
$itemData = (get-json "global_items.json").items
foreach ($item in $itemData) {
    $item | Add-Member -MemberType NoteProperty -Name "region_home" -Value $null -Force
    $items += $item
}

write-host 'Regions' -ForegroundColor green
$itemData = (get-json "regions.json").regions
foreach ($item in $itemData) {
    $regions += $item
}  

# write-host 'DOT' -ForegroundColor green
# for ($line -in (get-file "map.dot")) {
#     if ($line -match '^\s*"(.*?)"\s*->\s*"(.*?)"\s*\[label="(.*?)"\]') {
#         $room1 = $Matches[1]
#         $room2 = $Matches[2]
#         $direction = $Matches[3]
#         $roomConnections += [PSCustomObject]@{
#             from      = $room1
#             to        = $room2
#             direction = $direction
#         }
#     }
# }

foreach ($region in $regions) {
    write-host "Processing region: $($region.name) [ID: $($region.id)]"
    # Confirm/Create the folder for the region
    $region_folder = "$path\$($region.id)"
    if (-not (Test-Path -Path $region_folder)) {
        New-Item -ItemType Directory -Path $region_folder | Out-Null
        #write-host "Created folder for region: $region_folder"
    }
    $region_toml = @"
[[region]]
id                 = "$($region.id)"
name               = "$($region.name)"
description        = "$($region.description)"
recommended_level  = $($region.recommended_level)
admin_comment      = "$("Piece Effect: {0}  Notes: {1}" -f $($region.device_piece_effect, $($region.admin_comment)))"
"@
    Set-Content -Path "$region_folder\zone.toml" -Value $region_toml -Force
    #write-host ("Created region file for region: {0} [{1}]" -f $($region.name), "$region_folder\$($region.id).toml")

    # Build the rooms
    $room_toml = @"
# Zone id: $($region.id)
#

"@

    $region_rooms = $rooms | Where-Object { $_.region_home -eq $region.id }
    foreach ($room in $region_rooms) {
        ## Exits
        $exits = @()
        foreach ($e in $room.exits.psobject.properties) {
            $id = $rooms | Where-Object { ($_.region_home -eq $region.id) -and ($_.id -eq $e.Value) } | Select-Object -First 1
            $exits += "$($e.Name) = ""$($id.newid)"""
        }

        $room_toml += @"

[[room]]
id          = "$($room.newid)"
name        = "$($room.name)"
description = "$($room.description)"
coord       = []
exits       = {$($exits -join ", ")}
items       = [$(quote-and-join $room.item_ids)]
flags       = []
spawns      = [$(quote-and-join ($room.npc_ids + $room.enemy_ids))]

"@

        
    }
    Set-Content -Path "$region_folder\rooms.toml" -Value $room_toml -Force

    # Items
    $item_toml = @"
# Zone id: $($region.id)
#

"@
    $region_items = $items | Where-Object { $_.region_home -eq $region.id }
    foreach ($item in $region_items) {
        $item_toml += @"

[[item]]
id          = "$($item.id)" 
name        = "$($item.name)"
desc_short  = "$($item.description)"
desc_long   = ""
slot        = ""
weight      = 0
respawn     = false
no_drop     = false
tags        = []
effects     = []
value       = 0
on_get      = []

"@
    }
    Set-Content -Path "$region_folder\items.toml" -Value $item_toml -Force
    
    # NPCs
    $region_npcs = $npcs | Where-Object { $_.region_home -eq $region.id }
    $npc_toml = @"
# Zone id: $($region.id)
#
"@
    foreach ($npc in $region_npcs) {
        $npc_toml += @"
        
[[npc]]
admin_comment = ""
id          = "$($npc.id)"
name        = "$($npc.name)"
desc_short  = ""
desc_long   = ""
hostile     = false
tags        = []
style       = ""
style_prof  = 0
hp          = $($npc.hp)
max_hp      = $($npc.hp)
attack      = $($npc.attack)
defense     = $($npc.defense)
xp_reward   = 0
gold_reward = 0
dialogue    = ""
shop        = []
give_accepts = []
rest_cost   = 0
kill_script = []
respawn_time= 0

"@
    }
    Set-Content -Path "$region_folder\npcs.toml" -Value $npc_toml -Force

    # Quests
}
