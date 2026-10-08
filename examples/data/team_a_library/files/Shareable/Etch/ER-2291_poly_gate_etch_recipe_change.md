# ER-2291 Poly Gate Main-Etch Recipe Change (ETCH-07 Chamber B)

Change owner: Alice Kim, Etch Module, Process Engineering (alice.kim@contoso.com, mobile 010-1234-5678)
Change control board: PCCB-0914, approved 2026-09-04
Effective: 2026-09-08 for all NX-7 and NX-7L lots on ETCH-07 chamber B

## Background

After the August preventive maintenance on ETCH-07 chamber B, the post-etch poly gate critical dimension (CD) on NX-7 drifted from 31.8 nm to 33.4 nm against a target of 32.0 nm +/- 1.0 nm. Within-wafer CD uniformity degraded from 1.9% to 3.6% (3-sigma), with the largest deviation at the wafer edge. Optical emission data showed a slower main-etch rate and a late endpoint call on roughly one wafer in five.

## What changed

- Main-etch CF4/O2 flow changed from 48/12 sccm (4:1) to 50/10 sccm (5:1) to restore the lateral etch component.
- Source RF power reduced from 1250 W to 1180 W to recover a vertical sidewall profile.
- Bias power unchanged at 140 W; chamber pressure unchanged at 12 mTorr.
- Endpoint detection moved from the 405 nm emission line to the 520 nm line with a fixed 15% over-etch.
- Edge-ring temperature setpoint raised from 45 C to 50 C to reduce the edge CD bias.

## Qualification results

Twelve qualification wafers were processed across three NX-7 lots. Mean post-etch CD was 32.1 nm with 1.7% within-wafer uniformity (3-sigma). Gate-oxide pitting inspection found no increase versus the baseline recipe. The split lot W2291-Q showed a 2.3 point final-sort yield gain compared with the control split.

## Risks and controls

The lower source power reduces polymer deposition, so chamber wall conditioning is more sensitive to the seasoning sequence after maintenance. The SPC chart for post-etch CD now uses a tightened control limit of +/- 0.6 nm, and any out-of-control point holds the lot automatically. The change will be reviewed again after 500 production wafers.

## Contacts

Questions about this change go to the etch module team. Escalations after hours go to the on-call etch engineer at 010-1234-5678.
