$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$initialDirectory = [Console]::In.ReadToEnd()
Add-Type -AssemblyName System.Windows.Forms

$picker = [System.Windows.Forms.FolderBrowserDialog]::new()
$picker.Description = '选择聊天记录保存文件夹'
$picker.UseDescriptionForTitle = $true
$picker.ShowNewFolderButton = $true
$picker.SelectedPath = $initialDirectory
$selectedDirectory = $null
try {
    if ($picker.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        $selectedDirectory = $picker.SelectedPath
    }
} finally {
    $picker.Dispose()
}
[pscustomobject]@{ directory = $selectedDirectory } | ConvertTo-Json -Compress
