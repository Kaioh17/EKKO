; NSIS hooks (Tauri bundle.windows.nsis.installerHooks).
; The always-on backend (started at sign-in) holds files in the install
; folder, so stop it before installing or updating, and remove the
; sign-in task when ekko is uninstalled.

!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'schtasks.exe /End /TN "EKKO Listener"'
  nsExec::Exec 'taskkill.exe /F /IM ekko-backend.exe'
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  nsExec::Exec 'schtasks.exe /End /TN "EKKO Listener"'
  nsExec::Exec 'schtasks.exe /Delete /TN "EKKO Listener" /F'
  nsExec::Exec 'taskkill.exe /F /IM ekko-backend.exe'
!macroend
