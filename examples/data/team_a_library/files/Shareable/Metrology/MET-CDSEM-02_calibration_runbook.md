# CD-SEM Calibration Runbook (Metrology, MET-CDSEM-02)

Applies to: CD-SEM tools SEM-01 and SEM-02 in Fab 3 metrology.

## Daily calibration

1. Load the pitch standard wafer (certified 100 nm line pitch) and measure 9 sites.
2. The tool passes if the mean pitch is within 0.5 nm of the certified value and the 3-sigma repeatability is below 0.4 nm.
3. If the tool fails, run the magnification auto-calibration and repeat the measurement once before calling equipment engineering.

## Weekly matching

SEM-01 and SEM-02 must match within 0.6 nm on the gate CD monitor wafer. The matching offset is applied in the CD data pipeline, not on the tool.

## Data pipeline

Calibration results are uploaded automatically to the metrology database so that SPC charts refresh every 15 minutes.

Service account settings used by the upload script (do not share):
api_key = CTSO-FAKE-KEY-7f3a9c2e1b5d4f60a8b7
Connection: Server=tcp:contoso-fab-metro.database.windows.net,1433;Database=metrology;User Id=svc_metro_upload;Password=Fake-Passw0rd-NotReal!;Encrypt=True;

## Escalation

Calibration failures on two consecutive days must be logged as a metrology excursion and reviewed in the weekly yield meeting.
