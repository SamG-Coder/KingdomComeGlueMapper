param(
    [Parameter(Mandatory=$true)][string]$ModToolsPath,
    [Parameter(Mandatory=$true)][string]$TablesPath
)
# Read table XML through the official tools' serializer without launching an
# editor or game. This validates serialization, not in-game reference binding.
$ErrorActionPreference = 'Stop'
$null = [System.Reflection.Assembly]::LoadFrom((Join-Path $ModToolsPath 'Tools/Skald/Fasterflect.dll'))
$assembly = [System.Reflection.Assembly]::LoadFrom((Join-Path $ModToolsPath 'Tools/GeneratedDatabase/GeneratedDatabase.dll'))
$serializer = [System.Xml.Serialization.XmlSerializer]::new(
    $assembly.GetType('Database.sql_database'),
    [System.Xml.Serialization.XmlRootAttribute]::new('database'))
$failures = 0
$files = @(Get-ChildItem -LiteralPath $TablesPath -Recurse -Filter '*.xml' -File)
if ($files.Count -eq 0) { throw 'No database XML files found' }
foreach ($file in $files) {
    $reader = [System.Xml.XmlReader]::Create($file.FullName)
    try {
        $null = $serializer.Deserialize($reader)
        Write-Output ('PASS ' + $file.Name)
    } catch {
        $failure = $_.Exception
        while ($failure.InnerException) { $failure = $failure.InnerException }
        Write-Output ('FAIL ' + $file.Name + ': ' + $failure.Message)
        $failures++
    } finally { $reader.Dispose() }
}
if ($failures) { throw "$failures database XML files failed the native tools reader" }
Write-Output ("Validated {0} files using GeneratedDatabase.dll" -f $files.Count)
