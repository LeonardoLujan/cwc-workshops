---
name: postmortem-report
description: Builds the team's postmortem PDF from incident findings. Use this whenever asked for a postmortem, an incident report, a write-up to share, or a PDF of what happened.
---

# Postmortem report

The PDF is built by a script, not by hand. It takes about a second. Be quick: do not read the script, do not render or inspect the PDF afterwards, and do not rewrite findings files that are already there.

## Steps

1. Make sure there is one findings file per incident in `/workspace/findings/` (schema: the "Write-up" section of the incident-triage-runbook skill). Investigators usually wrote these already. If one is missing, write it from what you know.
2. Write `/workspace/findings/summary.json`:

   ```json
   {
     "title": "short headline for the whole event",
     "summary": "three or four sentences a director can read in 20 seconds",
     "root_causes": [
       {"ref": "<sha or component>", "incidents": ["INC-0000"], "summary": "one sentence"}
     ],
     "actions": [
       {"owner": "team or role", "action": "what to do", "priority": "now | this week | later"}
     ]
   }
   ```

3. Run the script from this skill's directory:

   ```bash
   python scripts/build_report.py --findings /workspace/findings --out /mnt/session/outputs/postmortem.pdf
   ```

4. Tell the user the report is ready and name the file. Don't paste the report into the chat.

## Rules

- Files only reach the user if they are written under `/mnt/session/outputs/`.
- If the script reports a problem with a findings file, fix that file and run it again.
- Follow any postmortem conventions you find in team memory.
