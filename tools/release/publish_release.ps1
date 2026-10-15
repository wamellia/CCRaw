param(
    [Parameter(Mandatory=$true)][string]$Repo,
    [Parameter(Mandatory=$true)][string]$Tag,
    [Parameter(Mandatory=$true)][string]$Notes,
    [Parameter(Mandatory=$true)][string[]]$Artifacts,
    [switch]$Publish
)
$ErrorActionPreference = 'Stop'
if ($Repo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Repo must be owner/repository.' }
if (-not (Test-Path -LiteralPath $Notes -PathType Leaf)) { throw 'Release notes file is required.' }
foreach ($artifact in $Artifacts) {
    if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw "Missing artifact: $artifact" }
}
# Only uploads prepared artifacts. Commits, branch pushes, tags and overwrites
# are separate maintainer actions; the default release is a reviewable draft.
$draftArguments = @('--draft')
if ($Publish) { $draftArguments = @() }
& gh release create $Tag @Artifacts --repo $Repo --verify-tag --title "CCRaw $Tag" --notes-file $Notes @draftArguments
if ($LASTEXITCODE -ne 0) { throw 'Release creation failed; no overwrite was attempted.' }
