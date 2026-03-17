param(
  [string]$ApiBase = "http://127.0.0.1:8000",
  [int]$BatchSize = 100,
  [int]$Concurrency = 8,
  [int]$MaxRounds = 0,
  [switch]$Refresh
)

$round = 0
$totalSuccess = 0
$totalFailed = 0

while ($true) {
  $round += 1

  $statsBefore = Invoke-RestMethod -Uri "$ApiBase/api/v1/disease/profile/stats"
  $missing = [int]$statsBefore.missing_malacards_profiles
  if ($missing -le 0) {
    Write-Output "No missing MalaCards profiles left."
    break
  }

  if ($MaxRounds -gt 0 -and $round -gt $MaxRounds) {
    Write-Output "Reached MaxRounds=$MaxRounds."
    break
  }

  $refreshFlag = if ($Refresh) { "true" } else { "false" }
  $uri = "$ApiBase/api/v1/disease/profile/backfill?limit=$BatchSize&offset=0&concurrency=$Concurrency&refresh=$refreshFlag"

  try {
    $result = Invoke-RestMethod -Method Post -Uri $uri
    $success = [int]$result.success
    $failed = [int]$result.failed
    $totalSuccess += $success
    $totalFailed += $failed

    Write-Output "round=$round missing_before=$missing queued=$($result.queued) success=$success failed=$failed total_success=$totalSuccess total_failed=$totalFailed"

    if ($result.queued -eq 0) {
      Write-Output "No new items queued. Stopping."
      break
    }
  }
  catch {
    Write-Output "Backfill round failed: $($_.Exception.Message)"
    Start-Sleep -Seconds 5
  }

  Start-Sleep -Milliseconds 500
}

$statsAfter = Invoke-RestMethod -Uri "$ApiBase/api/v1/disease/profile/stats"
Write-Output ("final_profile_count=" + $statsAfter.profile_count)
Write-Output ("final_missing_malacards_profiles=" + $statsAfter.missing_malacards_profiles)
