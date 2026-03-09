@echo off
REM termfix CMD hook — DOSKEY macros for frecency directory jumping
REM Installed via: termfix init cmd --confirm

REM j command — frecency directory jump
DOSKEY j=for /f "delims=" %%p in ('termfix jump $*') do @cd /d "%%p"

REM cd wrapper — record directory changes
DOSKEY cd=termfix cd-hook "$*" $T cd /d $*
