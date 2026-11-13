$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
$script:failures = 0

function Write-Check {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Detail
    )
    if ($Passed) {
        Write-Output "PASS: $Name - $Detail"
    }
    else {
        Write-Output "FAIL: $Name - $Detail"
        $script:failures++
    }
}

function Get-FileHashes {
    param([string]$Directory)
    $base = (Resolve-Path $Directory).Path.TrimEnd("\")
    $hashes = @{}
    Get-ChildItem -LiteralPath $base -File -Recurse | ForEach-Object {
        $relative = $_.FullName.Substring($base.Length + 1).Replace("\", "/")
        $hashes[$relative] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return ,$hashes
}

function Test-HashSetsEqual {
    param(
        [hashtable]$Left,
        [hashtable]$Right
    )
    if ($Left.Count -ne $Right.Count) {
        return $false
    }
    foreach ($key in $Left.Keys) {
        if (-not $Right.ContainsKey($key) -or $Left[$key] -ne $Right[$key]) {
            return $false
        }
    }
    return $true
}

$testOutput = & python -m pytest -q 2>&1
$pytestPassed = $LASTEXITCODE -eq 0
$testOutput | ForEach-Object { Write-Output $_ }
Write-Check "pytest" $pytestPassed "python -m pytest -q"

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("invoice-verify-" + [guid]::NewGuid().ToString("N"))
$first = Join-Path $tempRoot "first"
$second = Join-Path $tempRoot "second"
$generationPassed = $true
try {
    New-Item -ItemType Directory -Path $tempRoot | Out-Null
    foreach ($target in @($first, $second)) {
        & python -m src generate --output $target --seed 20261022 *> $null
        if ($LASTEXITCODE -ne 0) {
            $generationPassed = $false
        }
    }

    if ($generationPassed) {
        $firstHashes = Get-FileHashes $first
        $secondHashes = Get-FileHashes $second
        $generationPassed = Test-HashSetsEqual $firstHashes $secondHashes
        Write-Check "two seeded generations identical" $generationPassed "$($firstHashes.Count) generated files compared by SHA-256"
    }
    else {
        Write-Check "two seeded generations identical" $false "one or both generator commands failed"
    }

    $committedHashes = Get-FileHashes (Join-Path $root "data\generated")
    if ($generationPassed) {
        $freshMatchesCommitted = Test-HashSetsEqual $firstHashes $committedHashes
        Write-Check "fresh output equals committed data/generated" $freshMatchesCommitted "$($committedHashes.Count) files compared by SHA-256"
    }
    else {
        Write-Check "fresh output equals committed data/generated" $false "fresh generation failed"
    }
}
catch {
    Write-Check "seeded generation checks" $false $_.Exception.Message
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}

$labelColumns = @(
    "ground_truth",
    "issue_type",
    "defect_subtype",
    "expected_decision",
    "expected_route",
    "paired_invoice_id",
    "secondary_issue_types",
    "injection_rule"
)
$csvFiles = @()
foreach ($folder in @("input", "derived")) {
    $csvFiles += Get-ChildItem -LiteralPath (Join-Path $root ("data\generated\" + $folder)) -Filter "*.csv" -File
}
$labelFindings = @()
foreach ($file in $csvFiles) {
    $reader = [System.IO.StreamReader]::new($file.FullName, [System.Text.Encoding]::UTF8, $true)
    try {
        $header = $reader.ReadLine()
    }
    finally {
        $reader.Dispose()
    }
    foreach ($column in $header.Split(",")) {
        $name = $column.Trim().ToLowerInvariant()
        if ($labelColumns -contains $name -or $name.Contains("ground_truth")) {
            $labelFindings += ($file.FullName + ":" + $name)
        }
    }
}
Write-Check "no label columns in input headers" ($labelFindings.Count -eq 0) "$($csvFiles.Count) input/derived CSV headers scanned"
if ($labelFindings.Count -gt 0) {
    $labelFindings | ForEach-Object { Write-Output "  $($_)" }
}

$countCode = "import json,sqlite3; c=sqlite3.connect(r'data/generated/dataset.sqlite'); names=['vendor','purchase_order','purchase_order_line','goods_receipt','invoice']; print(json.dumps({n:c.execute('SELECT COUNT(*) FROM '+n).fetchone()[0] for n in names},sort_keys=True)); c.close()"
$countOutput = & python -c $countCode
$countCommandPassed = $LASTEXITCODE -eq 0
$expectedCounts = @{
    vendor = 300
    purchase_order = 1500
    purchase_order_line = 2200
    goods_receipt = 1800
    invoice = 2000
}
$countsPassed = $countCommandPassed
$countDetail = "could not query SQLite"
if ($countCommandPassed) {
    try {
        $actualCounts = $countOutput | ConvertFrom-Json
        foreach ($name in $expectedCounts.Keys) {
            if ([int]$actualCounts.$name -ne $expectedCounts[$name]) {
                $countsPassed = $false
            }
        }
        $countDetail = ($countOutput -join "")
    }
    catch {
        $countsPassed = $false
        $countDetail = $_.Exception.Message
    }
}
Write-Check "main table row counts" $countsPassed $countDetail

if ($script:failures -gt 0) {
    Write-Output "Verification failed with $($script:failures) failed check(s)."
    exit 1
}
Write-Output "All verification checks passed."
exit 0
