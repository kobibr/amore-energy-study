AmorE Energy Measurement — PRD v3.3
Energy Characterization of Pairing Delegation
AmorE on Cortex-M4: Crossover Analysis vs. Direct Pairing
Project Requirements Document
Version 3.3 — Friday, May 9, 2026
Author: Kobi Brener
Schedule: 7 weeks, 5 working days per week (target completion: late June 2026)
Status: Pre-execution — to be revised after pilot week
# 1. Executive Summary
This document specifies the requirements for a self-contained measurement study quantifying the energy cost of the AmorE pairing-delegation protocol on a constrained device, relative to direct on-device pairing. The study is the empirical follow-up to a previously published port of AmorE to Cortex-M4 with both BN254 and BLS12-381 curves, where wall-clock and cycle-count results have already been established.
The motivation is twofold. First, throughput is not the only or even the primary cost on battery-operated devices; energy is. AmorE's published timing analysis shows that delegation is slower than direct pairing in absolute wall-clock terms once verification overhead is included, yet the protocol may still be preferable when the metric of interest is joules per round under realistic operating conditions. Second, the literature on pairing-based cryptography on embedded devices contains extensive timing and code-size benchmarks but, to the author's knowledge, no quantified energy comparison between delegation and direct evaluation across protocol parameters and device duty cycles. This study aims to fill that gap.
The deliverables are: a reproducible measurement rig, a public dataset of energy traces, an analysis identifying any crossover regime where delegation is energy-preferable, a written technical report at arXiv-preprint quality, and a public repository containing all source code, calibration data, and analysis scripts. The intended audience is researchers in embedded cryptography and verifiable computation; the secondary audience is industrial groups designing battery-operated devices that require pairing-based primitives.
The expected duration is seven calendar weeks at five working days per week (35 working days). The work is funded and executed by the author; no external dependencies, grants, or co-authors are required to complete the study at the stated quality bar.
# 2. Background and Prior Work
## 2.1 The AmorE protocol
AmorE is a pairing-delegation protocol for amortizing the cost of multiple pairing computations on a constrained client by offloading the dominant work to an untrusted server, with a verification step at the client. The protocol guarantees correctness against a malicious server with high probability through a one-time setup phase and per-round randomized verification. The amortization regime is characterized by N, the number of pairings delegated per session.
The protocol's selling points, as reported by its authors, are: lower client-side cycle count for sufficiently large N; a small constant client memory footprint; and compatibility with standard pairing-friendly curves at multiple security levels. The trade-off is the verification step, which on a constrained device measurably reduces the throughput advantage versus direct on-device pairing.
## 2.2 Existing implementation: AmorE on Cortex-M4
The author's prior work ported AmorE from a desktop reference implementation to a Cortex-M4 device (STM32F407, 168 MHz, 192 KB RAM, 1 MB Flash), leveraging RELIC-toolkit as the underlying field and curve arithmetic library. Two curve instantiations were validated end-to-end:
- BN254 baseline: single-round AmorE wall-clock time of approximately 381.8 ms; direct pairing approximately 380 ms; cycle counts validated against the unmodified RELIC test harness.
- BLS12-381: single-round AmorE wall-clock time of approximately 1,919 ms; direct pairing approximately 523 ms; full validation suite (including a malicious-server test that exercised the verification path) passed without errors.
The BN254 work was upstreamed to RELIC as PR #317 and merged. Both implementations are public on GitHub. This prior work establishes the timing baseline; what it does not establish is the energy baseline, which is what the present study addresses.
## 2.3 The gap this study addresses
Pairing-based cryptography on embedded devices has been benchmarked extensively for time and space. Energy benchmarks for direct pairing exist; what does not exist, to the best of the author's knowledge, is a side-by-side energy comparison between a delegation protocol and direct evaluation, parameterized by both the protocol's amortization parameter (N), the device's duty cycle, and the communication channel.
This is not a minor gap. A device that performs one pairing per minute over a year-long deployment spends most of its time in low-power sleep states. The energy attributable to a single pairing event is therefore not just the active-mode energy of the computation; it includes radio energy for delegation traffic, wake-up energy, and any incidental costs that scale with the duty cycle. A protocol that wins on milliseconds may lose on joules, or vice versa, depending on the operating regime.
The primary research question is: under what combinations of N (rounds per session), duty cycle (active fraction of total time), and per-byte communication energy, does delegation become energy-preferable to direct pairing on a Cortex-M4-class device, for both BN254 and BLS12-381 curves?
# 3. Research Questions and Hypotheses
## 3.1 Primary research questions
- RQ1. What is the energy per delegated round of AmorE on STM32F407 as a function of N, for BN254 and BLS12-381?
- RQ2. What is the energy per direct pairing on the same device, for the same curves?
- RQ3. Does there exist a value N* such that for N > N*, AmorE is energy-preferable to direct pairing? If so, how does N* depend on curve choice?
- RQ4. How does the crossover N* shift as a function of device duty cycle, holding all other parameters constant?
- RQ5 (new in v3.0). How does the crossover N* shift as a function of per-byte communication energy? UART at 921600 baud is the directly measured baseline; the analysis projects to higher-energy channels (BLE, LoRa) using published per-byte energy figures and a datasheet-anchored cross-check (Day 9).
- RQ6 (new in v3.0). How sensitive is the energy comparison to supply voltage? Battery-operated devices experience voltage drop from ~3.3 V to ~2.7 V over their service life.
## 3.2 Operational hypotheses
The hypotheses below are stated to be falsifiable. The study reports the actual outcome regardless of which hypothesis is supported, or if none is.
- H1. AmorE per-round energy decreases monotonically with N, approaching an asymptote determined by the per-round verification cost plus the communication overhead.
- H2. Direct pairing per-round energy is approximately constant in N (small linear scaling from looping overhead only).
- H3. On BLS12-381, where direct pairing is significantly more expensive than on BN254, the crossover N* is smaller; AmorE wins energetically at lower N.
- H4. Increasing duty cycle (active fraction) shifts N* upward, because more time in active mode amortizes setup and verification overhead unfavorably for delegation.
- H5 (new). Communication energy is a non-negligible component of AmorE's per-round energy budget. Specifically, doubling the per-byte communication energy shifts N* upward by a measurable margin.
- H6 (new). Supply voltage in the 3.0–3.3 V range affects absolute energy but not the location of the crossover N*, because both modes scale similarly with voltage.
If H1–H6 are not supported, the analysis section reports the observed shape of the curves and any plausible explanation; null and negative results are explicitly in scope.
# 4. Scope
## 4.1 In scope
- Energy measurements of AmorE protocol rounds on STM32F407 with curves BN254 and BLS12-381, for N values across a defined sweep.
- Energy measurements of direct on-device pairing on the same hardware and curves, as a baseline.
- Measurement of idle, active-compute, and communication current draws sufficient to model duty-cycle effects.
- Measurement of per-byte UART communication energy, isolated from compute energy.
- A single-point validation measurement of Stop-mode quiescent current, to anchor the datasheet-based sleep model.
- A voltage-variation sub-sweep at 3.0 V on a representative subset of cells.
- Statistical analysis with confidence intervals computed from a variance-driven choice of repetition count.
- A reproducibility package: source code, build artifacts, raw measurement data, analysis scripts, and a written report.
## 4.2 Out of scope
- Energy measurements on hardware other than STM32F407.
- Curves other than BN254 and BLS12-381.
- Optimization of AmorE or RELIC for energy efficiency.
- Direct measurement of radio communication energy (BLE, LoRa, Wi-Fi); these are projected analytically from published per-byte figures, with the UART measurement serving as the empirical anchor.
- Side-channel security analysis of energy traces.
- Comparison against other pairing-delegation protocols beyond AmorE.
- Comprehensive characterization of all sleep modes; only Stop mode is measured directly. Standby is taken from datasheet with sensitivity analysis.
## 4.3 Assumptions
- The hardware setup is stable across measurement sessions; calibration is performed at the start of each session.
- The PPK2 instrument is operating within its rated specifications.
- Ambient temperature variations are within ±5 °C across all measurement sessions.
- The Raspberry Pi server's response time is dominated by computation, not by the lab network or filesystem.
- The STM32F407 in the author's possession is representative of typical samples; per-chip process variation is not characterized.
# 5. Methodology
## 5.1 Hardware setup
The measurement rig consists of three primary components, plus a host laptop running the analysis stack:

### 5.1.1 Power supply path
The PPK2 is wired in source-measure mode: it provides 3.3 V (or 3.0 V during the voltage sub-sweep) to the STM32F407G-DISC1's 3V3 rail through pin 2 of header JP1, after disconnecting the on-board LD3985M33R LDO via solder bridge SB1. This routes all current drawn by the STM32 through the PPK2's measurement path.
Note: The reference numbers JP1 and SB1 above are taken from the standard STM32F407G-DISC1 layout. Before any modification to the board, the exact identifiers and procedure must be verified against the official STMicroelectronics user manual UM1472, sections "Power supply" and "Power supply selection." The board may have minor revision differences. Day 1 of the schedule includes this verification step.
The VBAT pin is reserved for RTC backup only and is not used as a primary supply input. An alternative measurement topology — feeding the PPK2 through the board's USB connector (CN1) and measuring VBUS — was rejected because it would also include the on-board ST-Link's current in the trace, biasing the measurement. The chosen topology isolates the STM32 application processor cleanly.
The STM32 is programmed to set GPIO trigger lines (PA0, PA1, ) high at the start of each protocol phase and low at the end. The PPK2 captures these on its digital input channels and aligns them with the current trace.  is reserved for the dedicated UART-isolation triggers used in section 5.2.2. Communication between the STM32 and the Raspberry Pi server is over UART at 921600 baud, matching the firmware configuration documented in SYSTEM_DOC.md from PR #317; lower baud rates were considered but rejected as artificially extending the UART window beyond what the production firmware does. The host laptop is connected to both the PPK2 and to the STM32's ST-Link debug port over USB; the ST-Link USB provides debug and program access only, never primary supply current to the application processor.
## 5.2 Software harness
The DUT firmware is the existing AmorE port from the prior project, unmodified except for the addition of GPIO trigger lines around each measurable phase. The instrumentation is non-invasive: the GPIO toggles add at most a few cycles per phase boundary, well below the noise floor of the energy traces. The firmware exposes three operating modes selectable at compile time:
- Mode A (AmorE client): executes the full AmorE protocol with N rounds, using the Raspberry Pi server. Triggers fire around setup, blind, server-wait, verify, and round-summary phases.
- Mode B (direct pairing): computes N pairings sequentially on the DUT, using the same field and curve arithmetic and the same RELIC build. Triggers fire around each pairing and around the full sequence.
- Mode C (UART isolation, new in v3.0): transmits a fixed payload of K bytes over UART without performing any cryptographic computation. Used in section 5.2.2 to isolate per-byte communication energy.
A fourth low-power baseline mode is available for measuring quiescent current under controlled idle and Stop states; this is required to isolate the active-only energy of the protocol from the standing draw of the device, and to anchor the sleep-state model (section 5.4.3).
### 5.2.1 Phase boundary definitions
To ensure consistent phase attribution across all traces and analyses, phase boundaries are defined as follows:
- Setup. From the first instruction of OneTimeSetup() to the return from OneTimeSetup(). Trigger PA0 high on entry, low on return.
- Blind. From the first instruction of the per-round blind() function to the byte that initiates UART transmission to the server. Trigger PA0 high on entry, low immediately before the UART HAL call.
- ServerWait. From the byte that initiates UART transmission to the byte received on UART completion of server response. Trigger PA1 high before transmit, low after receive.
- Verify. From the first instruction after ServerWait completes to the per-round verify() return. Trigger PA0 high on entry, low on return.
- Summary. From verify() return to the next round's blind() entry. Implicit; computed as the gap between phase markers.
### 5.2.2 Communication-energy isolation (revised in v3.1)
Mode C is decomposed into three sub-modes to correctly isolate per-byte UART energy from CPU energy. A bare "UART transmit while CPU is otherwise idle" measurement does not isolate the UART contribution, because a CPU in a tight wait loop still draws current. v3.1 corrects this by measuring three sub-modes:
- Mode C-idle. CPU in a tight `while(1) { __NOP(); }` loop, UART silent. Trigger PA2 high for the measurement window, low at the end. Provides E_idle per unit time.
- Mode C-TX. CPU transmits a payload of K bytes via blocking HAL_UART_Transmit, then enters the same idle loop. PA2 high before the HAL call, low after the HAL call returns. Provides E_TX(K).
- Mode C-RX. CPU receives K bytes via blocking HAL_UART_Receive (with the Pi sending). PA2 high before the HAL call, low after. Provides E_RX(K).
The per-byte energies are computed by subtracting the idle baseline over the same trigger window:
e_byte_TX = (E_TX(K) − E_idle(t_TX(K))) / K
e_byte_RX = (E_RX(K) − E_idle(t_RX(K))) / K
where t_TX(K) and t_RX(K) are the measured durations of the TX and RX windows, used to scale the idle baseline to the same time interval. K is varied across {16, 64, 256, 1024} bytes; per-byte energy is extracted by linear fit (slope is the asymptotic per-byte energy, intercept captures wake-up and HAL overhead).
The Mode A energy decomposition then explicitly separates communication from compute energy:
E_AmorE_round = E_blind_compute + (bytes_TX × e_byte_TX) + (bytes_RX × e_byte_RX) + E_verify_compute
This allows the AmorE-vs-direct comparison to be projected to other communication channels (BLE, LoRa) by substituting the per-byte energy figure. A discussion of representative channel figures from published literature accompanies the projection in section 5.5; v3.1 also adds a datasheet-anchored projection (Day 9) alongside the literature figures.
## 5.3 Measurement protocol
Each measurement session begins with a fresh power-up of the DUT, a 30-second warm-up under quiescent load, and a calibration check against the previous session's idle-current reading (tolerance: ±5%). The session then proceeds through the test matrix defined in section 5.4, with N_reps repetitions per cell where N_reps is determined by the variance characterization in section 5.4.4. Between cells, the device is reset and idled for at least 5 seconds to suppress thermal coupling. Cells within a session are randomized within duration bins, not globally: cells are grouped into bins by expected runtime (short: < 1 minute; medium: 1–10 minutes; long: > 10 minutes), and randomization happens within each bin. This controls for thermal coupling — a long BLS12-381 N=200 cell heats the chip non-trivially and would bias an immediately-following short cell — while preserving the bias-reduction benefit of randomization within comparable conditions. Raw traces are exported as CSV via the PPK2's host-side API, with timestamps preserved.
Energy per region is computed as the time integral of instantaneous power over the region, with power computed as the product of the configured supply voltage and the measured current. Confidence intervals are computed from the repetitions per cell using standard error of the mean.
## 5.4 Test matrix

Total primary cells: 2 curves × (7 + 4) configurations = 22 base cells. With repetitions, voltage sub-sweep, UART isolation, and Stop validation, the full sweep is approximately 90–110 measurement instances. Estimated wall-clock time for the full sweep, including pad and cooldown, is approximately 50–70 hours, distributed across 10–14 working sessions, with substantial use of unattended overnight runs.
### 5.4.1 Justification for modeled (rather than measured) sleep energy
Direct measurement of all duty-cycle scenarios would require characterizing the device's sleep states (Stop, Standby) under controlled wake-up conditions across the full duty-cycle parameter space, which is outside the core scope of this study. The active-mode energy is measured precisely; sleep contributions are modeled from STM32F407 datasheet values for IDD_STOP and IDD_STANDBY at the relevant supply voltage, anchored by a single ground-truth measurement of Stop-mode current on the actual DUT (section 5.4.3), with a sensitivity analysis (±10% perturbation around the datasheet values) reporting how the conclusions shift under that uncertainty.
v3.3 update: while comprehensive characterization across all duty cycles remains out of scope, two anchor measurements are now performed on Day 9 — a 1-hour Stop-mode quiescent current measurement and a 100-cycle wake-up energy burst — which together remove the need to model the per-wake-up cost from datasheet (the most uncertain term in the v3.0 model, since STM32F407 specifies wake-up time as a maximum, not a typical). Sleep-mode current and per-wake-up energy are both measured; only Standby-mode current and the choice of duty-cycle scenarios remain modeled.
### 5.4.2 Voltage variation sub-sweep (new in v3.0)
To characterize sensitivity to supply voltage, a sub-sweep is performed at 3.0 V (representing a partially discharged battery) on a representative subset of cells: N ∈ {10, 50, 100} for both curves and both modes (12 cells, with the same repetition policy as the main sweep). The sub-sweep is sufficient to determine whether the crossover N* moves materially between 3.3 V and 3.0 V, and to compute a first-order voltage sensitivity coefficient. A full voltage sweep (e.g., 3.3 V, 3.15 V, 3.0 V, 2.85 V) is out of scope; the chosen two-point design is the minimum that supports a quantitative claim.
### 5.4.3 Sleep-state ground-truth measurements (revised in v3.3)
Two ground-truth measurements anchor the sleep model. Both are performed on Day 9 of the schedule and analyzed on Day 10. They convert the v3.0 datasheet-only sleep model into a partially measured one, with reduced uncertainty in the most consequential terms.
(1) Stop-mode quiescent current measurement
The DUT is placed in Stop mode using STM32 HAL_PWR_EnterSTOPMode() with the RTC running, and PPK2 captures current for 1 hour. The PPK2 noise floor in low-current mode is empirically characterized in the same session by capturing 30 minutes of trace with no DUT load (PPK2 sourcing 3.3 V into open). The ratio of measured-mean to noise-floor (SNR) determines reporting:
- SNR > 5×: report as a point estimate (e.g., "measured 0.62 µA, datasheet typical 0.6 µA at 3.3 V, 25 °C").
- 1× < SNR ≤ 5×: report as an upper bound only; the model uses the upper bound as a worst-case anchor for IDD_STOP.
- SNR ≤ 1×: the measurement does not constrain the model; the datasheet typical value is retained with widened sensitivity bounds, and the limitation is documented.
If the measured value diverges from the datasheet typical by more than 30%, the sensitivity analysis bounds are widened accordingly, and the discrepancy is investigated (firmware path, board issue, ambient temperature) before the model is locked.
(2) Wake-up energy burst measurement
The DUT performs 100 consecutive Stop→Run→Stop cycles with RTC alarm wake-up at ~10 ms intervals, while PPK2 captures the full burst window. Total burst energy is integrated and divided by 100 to obtain E_wakeup, the per-transition wake-up energy. Expected magnitude: ~10–30 µJ per wake-up at 168 MHz / 3.3 V, dominated by the ~13 µs wake-up latency at near-Run current. Total wall-clock cost: approximately 5 minutes including firmware build.
Without this measurement, E_wakeup must be modeled from STM32F407 datasheet values, where wake-up time is specified only as a maximum (not a typical) — the most uncertain term in the v3.0 sleep model. Direct measurement reduces this uncertainty to ±5–10% (set by the PPK2 active-range accuracy and the 100-cycle averaging).
Sleep model
The total session energy used in all duty-cycle calculations is:
E_total = E_active + (T_sleep × IDD_sleep × V) + (n_wakeups × E_wakeup)
where:
- E_active is the measured active-mode energy of the protocol cells (Modes A and B), summed across the rounds in the session.
- T_sleep is the duty-cycle-derived sleep duration.
- IDD_sleep is the validated Stop-mode current from measurement (1) above, or its upper bound.
- V is the supply voltage (3.3 V or 3.0 V depending on sub-sweep).
- n_wakeups is the number of Stop→Run transitions implied by the duty-cycle scenario.
- E_wakeup is the measured per-transition wake-up energy from measurement (2) above.
This formula is implemented in `analysis/sleep_model.py` (Day 10). The IDD_STANDBY contribution is included as an additional term only for duty-cycle scenarios at or below 0.01% active fraction, where Standby is the relevant sleep mode rather than Stop; this term is taken from datasheet only with explicit ±10% sensitivity.
### 5.4.4 Variance characterization and per-cell repetition count (revised in v3.1)
On Day 7, after the basic measurement and analysis pipeline is in place, a single cell (BN254, Mode A, N=10) is measured 100 times in succession with no operator intervention (an overnight unattended run). The energy per round is computed for each of the 100 repetitions, and the standard deviation σ across the 100 reps is reported.
From σ and the desired half-width of the 95% confidence interval (target: ≤ 2% of the mean), the required N_reps per cell is computed as N_reps ≥ (1.96 × σ / target_half_width)². If N_reps ≤ 3 satisfies the target, the original v2.0 plan of 3 reps is retained. If 3 < N_reps ≤ 10, all production cells are run with N_reps repetitions. If N_reps > 10, the methodology is reconsidered (likely indicating an instrumentation or thermal issue) before the production sweep begins.
v3.1 confirmation step: a single-cell variance characterization assumes the variance is approximately uniform across the test matrix. To check this assumption, on Day 8 a 20-rep confirmation run is performed on (BLS12-381, Mode A, N=10), and the variance σ_BLS is compared to σ_BN254. Three outcomes are possible:
- Variances comparable (within ±25%). A single uniform N_reps is used for all production cells.
- Variances differ by 25–100%. Two distinct N_reps values are computed (one per curve) and used in the production sweep.
- Variances differ by more than 2×. Investigate before proceeding; this likely indicates a curve-specific issue (thermal coupling on long cells, memory-pressure variation, etc.) that the methodology must address.
This converts the choice of repetition count from an assertion into a measured quantity, which is the standard expected at conference-quality level.
## 5.5 Data analysis
The primary analytical artifacts are:
- Energy per round versus N. AmorE on each curve, with direct-pairing energy as a horizontal reference line. Confidence-interval bands shown.
- Crossover map in (N, duty-cycle) plane. Contours separating regions where delegation is energy-preferable from regions where direct pairing is.
- Crossover sensitivity to communication channel (new). Same crossover map projected for BLE-class and LoRa-class per-byte energy figures, using the UART measurement as the empirical anchor.
- Voltage sensitivity. Reported as a single coefficient ∂N* / ∂V from the 3.0 V vs 3.3 V comparison, with the caveat that two points support only a linear extrapolation.
- Phase-resolved energy breakdowns. Setup, blind, server-wait, verify, summary; per curve and per N.
- Sensitivity analysis on sleep model. How conclusions shift under ±10% perturbation of (a) the validated IDD_STOP value and (b) the measured E_wakeup value, independently and jointly. Reported as four-corner sensitivity (low-low, low-high, high-low, high-high) on the crossover map. The analysis identifies the duty-cycle regime where the qualitative conclusion is robust to both perturbations simultaneously; outside that regime, the conclusion is reported with explicit uncertainty bounds.
All analysis is performed in Python using NumPy, pandas, matplotlib, and scipy (for confidence interval computation). Scripts are version-controlled. The full pipeline from raw CSVs to finished plots is reproducible by running a single make target.
## 5.6 Reproducibility
The reproducibility package, published under the same license as the existing AmorE port, contains:
- Firmware source code with the GPIO instrumentation, including the exact RELIC commit and toolchain version used.
- Server-side scripts for the Raspberry Pi.
- PPK2 control scripts (Python, using the IRNAS/ppk2-api-python library), including the watchdog/resume logic specified in section 7.3 Day 6.
- Raw CSV traces for every measurement cell, with annotations.
- Analysis scripts that regenerate every figure and table in the report.
- A bill of materials with part numbers and a wiring diagram.
- Calibration logs from each measurement session, including the variance characterization log and the Stop-mode validation log.
# 6. Instrumentation Justification
The choice of PPK2 over higher-precision instruments (e.g., Otii Arc Pro at approximately 13× the cost) deserves justification. The argument has three parts.
First, the relevant accuracy floor is set by the magnitude of the effect under measurement, not by the instrument's specification. The currents drawn by the STM32F407 in active mode are in the 50–200 mA range, and the per-pairing energies for BLS12-381 are in the hundreds of millijoules. The PPK2's stated accuracy in this current range is approximately ±5%, corresponding to ±2.5–10 mA absolute, well below the differences expected between AmorE configurations across the test matrix. Averaging over the variance-determined repetitions, and over per-round measurements where each round contains thousands of arithmetic operations, tightens the effective uncertainty further.
Second, the PPK2's sample rate (100 ksps) is approximately 25× higher than the Otii Arc Pro's (4 ksps). For a study where phase-resolved energy attribution is important — separating setup from blind from verify — the higher sample rate is more valuable than the lower static accuracy. The PPK2 is the better instrument for this specific task on technical grounds, independent of cost.
Third, the PPK2 is widely used in published research on embedded energy benchmarking. Citing it as the instrument is, in the relevant subfield, neutral with respect to credibility. The PPK2 meets the accuracy requirements set by the magnitude of the effects under measurement, with sample-rate advantages relevant to phase-resolved attribution.
The reproducibility package documents the calibration procedure, the per-session idle-current sanity check, and the Stop-mode anchoring measurement, which together establish that the instrument was operating correctly throughout the study.
# 7. Timeline and Deliverables
## 7.1 Seven-week schedule (overview)

Schedule extension policy 
The 7-week target is the minimum, not the maximum. If Day 7 (variance) or Day 9–10 (Stop-mode validation) surfaces a methodology issue requiring iteration — for example, σ_BLS substantially larger than σ_BN254 indicating a thermal coupling problem, or Stop-mode current grossly inconsistent with datasheet — the schedule extends rather than the methodology compresses. The first week to absorb extension is Week 5 (slack/cleanup); the second is Week 6 (writing). Extension beyond Week 7 is documented in `` with the reason. Cutting methodology to meet schedule is explicitly forbidden by this policy.

## 7.2 Deliverables and acceptance criteria
D1. Technical report (PDF), arXiv preprint quality
8–14 pages, single-column, with abstract, introduction, methodology, results, discussion, reproducibility statement, and references. LaTeX source included in the repository. The framing target is arXiv preprint quality: rigorous and citable, but not gated on conference review.
Acceptance criteria:
- Abstract is 200 words or fewer.
- Methodology section is reproducible by an external reader without reference to additional documents (other than UM1472 and standard datasheets).
- At least 5 figures: energy vs. N (per curve), crossover map, communication-channel projection, voltage sensitivity, phase breakdown.
- Every numerical claim in the report is traceable to a specific cell or set of cells in the raw data.
- The reference list includes at least 5 citations from IACR, CHES, or comparable embedded-cryptography venues from 2018–2026.
- Confidence intervals are reported on every numerical result, not just point estimates.
- Sleep model is anchored by the Day 9–10 measurements (Stop-mode current and wake-up energy burst), reported in the methodology and discussed in the results.
- Variance-determined repetition count is reported and justified.
D2. Public repository
Source code, raw data, analysis scripts, calibration logs, BOM and wiring diagram, with a README sufficient for an external party to reproduce the study.
Acceptance criteria:
- Repository follows the directory layout in Appendix E.
- README provides a step-by-step walkthrough that an embedded engineer can follow to reproduce the rig and run a representative subset of cells in under one working day.
- All raw CSV traces from production sweep cells are present in measurement/traces/ (or, if their total size exceeds 100 MB, hosted externally with a manifest in the repo).
- A `make figures` target exists and regenerates every figure in the report from the raw data.
- The watchdog/resume design is documented and tested.
- LICENSE file is present and matches the parent project's license.
D3. Outreach email
Drafted on Day 35 from actual findings (not from this PRD). See section 7.5 for the policy. The acceptance criteria for this deliverable are listed in section 7.5 and not duplicated here.
Section 7.3 below contains the full day-by-day plan that maps to these deliverables.
## 7.3 Day-by-day plan (35 working days)
This plan covers 35 working days (7 weeks × 5 days). Each day specifies its primary objective, expected outputs, and a checklist of activities. Days are not strictly sequenced beyond the week boundary; if a day's work finishes early, the next day's work may begin. If a day's work runs over, the slack day at the end of each week absorbs it.
The legend below applies throughout:
- Each day has a target output that should exist by end of day. If it doesn't, that day is marked yellow in retrospective tracking.
- "Sanity check" means: produce a value, compare to a prior-known value, and decide pass/fail before proceeding.
- "Commit" means: state of code/data is checkpointed in git with a meaningful message.
- NIGHT RUN: where applicable, the day notes a script or measurement that can run unattended overnight (typically 8–14 hours), advancing the project without consuming the next day's work hours. Physical measurement is the schedule bottleneck; night runs are how the schedule fits.



































































































## Week 1 — Equipment, Board Preparation, Firmware Instrumentation
### Day 1 — Equipment receipt, UM1472 verification, bench layout
Objective: confirm that all equipment is functional and that the board-modification plan in Appendix B is correct for the specific board revision in hand.
- Unbox and visually inspect PPK2, cables, hub, jumpers.
- Install nRF Connect for Desktop on the host laptop. Run the Power Profiler app's built-in self-test.
- Power up the STM32F407G-DISC1 from USB (no PPK2 yet) and confirm the existing AmorE firmware boots.
- Open UM1472. Find the "Power supply" and "Power supply selection" sections. Identify the actual jumper and solder-bridge identifiers for the specific board revision.
- Cross-check Appendix B of this document against UM1472 and update if required.
- Sanity check: STM32 boots and runs prior firmware. PPK2 shows ~0 mA when no DUT is connected.
Output: bench laid out, all components inventoried, Appendix B verified.

### Day 2 — Rehearsal: SB1 cut on spare board, baseline trace on primary
Objective: perform the irreversible board modification on the SPARE board first as a rehearsal; capture a pre-modification baseline current trace on the primary.
- Photograph the spare STM32F407G-DISC1 (top and bottom).
- Practice cutting SB1 on the spare board using the procedure verified on Day 1. Confirm with multimeter that the on-board LDO output is no longer connected to the 3V3 rail on the spare.
- Wire PPK2 in source-measure mode to the spare board's 3V3 rail. Verify spare boots and runs idle firmware.
- If the spare modification went smoothly: the primary modification on Day 3 is low-risk. If it went badly (damaged trace, accidental short, board fails to boot): treat the spare as a learning experience, refine the procedure, and document the refined procedure in `docs/board-modification/` before touching the primary.
- Switch back to the primary board: wire PPK2 in pass-through (ammeter) mode in series with the laptop USB cable to the primary STM32. Boot existing AmorE firmware, run a single round, capture trace.
- Manually inspect the primary trace: identify idle, active, UART communication regions.
- Sanity check: spare boots cleanly under PPK2 source-measure. Primary idle current is in the expected ~30–80 mA range under USB pass-through.
Output: spare board successfully modified or refined procedure documented; pre-modification baseline trace on primary saved as `measurement/traces/.csv`.

### Day 3 — Board modification on primary (procedure rehearsed on Day 2)
Objective: cut SB1 on the primary board using the procedure already rehearsed on the spare; confirm measurement isolates the application processor.
- Photograph the primary unmodified board (top and bottom).
- Cut the solder bridge on the primary, following the procedure refined on Day 2. Confirm with multimeter that the on-board LDO output is no longer connected to the 3V3 rail.
- Wire PPK2 VOUT to 3V3 rail (JP1 pin 2 in standard layout). Wire PPK2 GND to board GND.
- Configure PPK2 to source 3.3 V.
- Power on. Confirm STM32 boots.
- Sanity check: idle current is now noticeably lower than Day 2 (the ST-Link is on a separate USB and no longer counted). Expected: ~20–50 mA.
- Spare board (already modified on Day 2) is reserved as a known-good fallback in case of primary-board damage or anomalies during the production sweep.
Output: primary modified-board trace saved as `measurement/traces/.csv`. Photos archived in `docs/board-modification/`. Both primary and spare are now in source-measure mode.

### Day 4 — GPIO trigger firmware (Mode A development)
Objective: add GPIO trigger lines to the AmorE client firmware around each measurable phase, and validate against the PPK2's digital inputs.
- Branch the firmware repo: `feature/energy-instrumentation`.
- Add helper macros: `TRIG_PHASE_BEGIN(phase_id)` and `TRIG_PHASE_END(phase_id)`.
- Instrument Mode A (AmorE client): triggers around Setup, Blind, ServerWait, Verify, Summary, per the boundary definitions in section 5.2.1.
- Wire PPK2 GPI 1, 2, 3 to STM32 PA0, PA1, .
- Run a single round, observe trigger transitions in the PPK2 trace.
- Sanity check: each phase boundary appears at the expected time relative to the current trace.
Output: instrumented Mode A firmware committed; trace `measurement/traces/.csv` shows aligned phase boundaries.
NIGHT RUN: preliminary stability trial — repeatedly run a single Mode A round (BN254, N=1) for ~50 reps with a hand-coded loop, log the per-rep timing variability via DWT cycle counter to UART. This is not yet an energy measurement (run_cell.py is not built), but it confirms the firmware is repeatable enough for tomorrow's automated measurement to be meaningful. Save log as `measurement/.log`.

### Day 5 — Mode B and Mode C firmware
Objective: implement Mode B (direct pairing) and Mode C (UART isolation) firmware variants, compile-time selectable.
- Implement Mode B: a loop computing N direct pairings using the same RELIC build as Mode A. Compile-time switch.
- Add GPIO triggers around each pairing and around the full N-loop.
- Implement Mode C: transmit K bytes over UART (TX-only and RX-only variants), compile-time K.
- Triggers PA2 high before transmit, low after. For RX, triggers high before HAL_UART_Receive and low after.
- Run Mode B with N=1, observe trace. Run with N=3, observe.
- Run Mode C with K=64, observe.
- Sanity check: per-pairing energy is approximately constant across N=1 and N=3 (Mode B). UART transmission visible as a distinct current shape (Mode C).
- End-of-week review: did anything in Days 1–4 surface a flaw in the methodology? If yes, document it and adjust week 2.
Output: Mode B and Mode C firmware committed; both modes confirmed working; Week 1 retrospective entry in ``.

## Week 2 — Methodology Lock-in: Variance, Calibration, Stop Validation, Comm Energy
### Day 6 — PPK2 Python automation with watchdog/resume + host stability
Objective: write the script that drives the PPK2, instructs the STM32 to run a specified cell, captures the trace, and saves a labeled CSV — including watchdog and resume logic AND verified host-side stability for unattended overnight runs.
- Install ppk2-api-python locally (`pip install ppk2-api`).
- Write `run_cell.py(curve, mode, N, repetition, voltage)`: configure PPK2, send UART command to STM32 to start cell, wait for completion (detected by trigger return-to-idle), read trace, save CSV with structured filename `<curve>_<mode>_N<n>_V<v>_rep<r>.csv`.
- Watchdog: a timeout proportional to the expected cell duration. If exceeded, mark cell as failed, log diagnostic info, reset DUT, continue to next cell.
- Resume: at startup, read existing CSV files in `measurement/traces/`, build the set of completed (cell, voltage, rep) tuples, skip those when running a sweep.
- Host-side stability: disable host laptop sleep (caffeinate on macOS / systemd-inhibit or systemd-run on Linux); disable USB autosuspend for the PPK2 and the ST-Link USB devices (udev rules on Linux, Power Options on Windows); confirm nRF Connect background services are not in the path of the trace capture.
- Run a single cell. Manually verify the saved CSV is valid and contains the expected phase boundaries. Then deliberately kill the script mid-cell and verify resume picks up cleanly.
- Acceptance criterion (new in v3.1): a 12-hour unattended run completes without operator intervention. This run can be a loop of cheap cells (BN254 N=1, repeated). If it doesn't complete cleanly, the watchdog is not yet ready and the production sweep cannot begin.
Output: `run_cell.py` committed with watchdog/resume; host stability configured; one automated trace saved successfully; resume verified.
NIGHT RUN: host stability validation — the 12-hour run from the acceptance criterion above doubles as the host-stability test. Output: confirmation in `` that 12 hours of unattended operation completes.

### Day 7 — Calibration procedure + variance characterization design
Objective: document a per-session calibration procedure and prepare the night's variance characterization run.
- Review the  idle-drift data. If drift > 5%, diagnose (USB power instability, PPK2 thermal issue, host laptop sleep) before proceeding.
- Write `calibrate.py`: powers DUT in idle mode, samples for 30 seconds, computes mean and standard deviation of idle current, writes to `measurement/calibration-logs/<timestamp>.json`.
- Run calibration three times across the day; compare drift.
- Sanity check: idle-current standard deviation across three calibrations within ±5% of the mean.
- Document the calibration procedure in `docs/methodology.md`.
- Prepare `variance_study.py`: invokes `run_cell.py` 100 times for (BN254, Mode A, N=10), saves traces to `measurement/traces/variance_study/`.
Output: `calibrate.py` and `variance_study.py` committed; methodology section drafted.
NIGHT RUN: variance characterization sweep — execute `variance_study.py`. 100 reps × ~400 ms per rep ≈ 40 seconds of compute, but with cooldown intervals it becomes ~3 hours; even with conservative spacing, this completes well before morning. Output: 100 trace CSVs in `measurement/traces/variance_study/`.

### Day 8 — Trace parser, energy computation, variance analysis, BLS variance check
Objective: write the analysis modules and apply them to last night's variance study to determine N_reps; cross-check on BLS to confirm σ generalizes.
- Write `analysis/parse_traces.py`: load CSV → numpy arrays → identify GPIO transitions → segment current and voltage arrays per phase.
- Write `analysis/compute_energy.py`: integrate power over each phase, return a dict of `{phase: energy_mJ}`.
- Write `analysis/variance_summary.py`: load all 100 traces from variance_study, compute per-rep total energy, compute σ, compute required N_reps for 2% target half-width per section 5.4.4.
- Run on the variance_study data. Report σ (BN254) and N_reps in `docs/methodology.md`.
- Cross-curve validation (new in v3.1): run 20 reps of (BLS12-381, Mode A, N=10) using `run_cell.py`. ~20 reps × ~30 seconds per rep ≈ 10 minutes. Compute σ for BLS.
- If σ_BLS / σ_BN254 < 2: lock a single global N_reps; document this finding.
- If σ_BLS / σ_BN254 ≥ 2: lock per-curve N_reps values; document the disparity as a finding for the report.
- Sanity check: total energy = sum of phase energies (within 1% rounding) for at least 5 randomly sampled traces.
- Decision point: lock the production N_reps based on the result.
Output: parser and energy modules committed; first phase-resolved energy table; BN254 and BLS σ both reported; production N_reps determined and locked.
NIGHT RUN: communication isolation pre-sweep — Mode C-idle, C-TX, C-RX cells: K ∈ {16, 64, 256, 1024}, both TX and RX directions, plus C-idle baselines, 5 reps each ≈ 50 cells × ~30 seconds per cell = ~30 minutes; trivial overnight load. Output: traces in `measurement/traces/comm_isolation/`.

### Day 9 — Communication energy fit, datasheet anchoring, Stop + wake-up validation
Objective: compute per-byte UART energy from last night's data; anchor BLE/LoRa projections to modern datasheets; perform the two Day 9 ground-truth measurements that anchor the sleep model — Stop-mode quiescent current (NIGHT RUN) and per-transition wake-up energy.
- Write `analysis/comm_energy_fit.py`: load Mode C-idle, C-TX, C-RX traces; subtract C-idle baseline from C-TX and C-RX as specified in section 5.2.2; plot residual energy vs K for TX and RX; fit slope (per-byte) and intercept (overhead).
- Report per-byte TX and RX energies in `docs/methodology.md`.
- Sanity check (expanded in v3.2): the reported per-byte figure is the residual energy after C-idle subtraction (the slope of the linear fit), not the total energy in the TX window. Expected magnitude is on the order of (I_active − I_idle) × V × t_byte. At 921600 baud, t_byte ≈ 10.85 µs; with a realistic Δ-current of 3–15 mA (UART peripheral + HAL polling overhead above NOP idle) at 3.3 V, the slope should land in the ~0.1–0.5 µJ/byte range. Values > 5 µJ/byte suggest the C-idle baseline is contaminated (e.g., the idle path inadvertently leaves UART in RX-listen state); values < 0.05 µJ/byte suggest the K range is too narrow for a clean slope. Total per-byte energy in the TX window is ~10–30× the residual; the residual, not the total, is what is reported.
- Datasheet-anchored channel projections (new in v3.1): pick one modern BLE chip (e.g., Nordic nRF54L15 or equivalent) and one modern LoRa chip (e.g., Semtech SX1262 or equivalent). For each, compute per-byte energy directly from the datasheet using Tx_current × Tx_time × supply_voltage. Document calculation in `docs/comm_anchors.md`. ~2 hours, including chip selection rationale.
- Compare datasheet-anchored values with literature values from your reference list. If within 2× of each other, channel projection rests on two independent sources. If divergent, document the discrepancy and use both as bounds.
- Prepare `stop_validation.py`: places DUT in Stop mode using HAL_PWR_EnterSTOPMode(), captures PPK2 trace for 1 hour. Empirical PPK2 noise floor must be characterized first by capturing 30 minutes of trace with no DUT load (PPK2 sourcing 3.3V into open).
- Verify firmware support for HAL_PWR_EnterSTOPMode() works on this DUT in a manual short test (5-minute trial) — confirm the chip enters Stop and the PPK2 sees a current drop.
- Wake-up energy burst measurement (new in v3.2; methodology in section 5.4.3 part 2): build a short firmware variant that performs 100 consecutive Stop→Run→Stop cycles using the RTC alarm to wake at ~10 ms intervals. Capture the PPK2 trace, integrate over the burst window, divide by 100 to obtain E_wakeup. Expected magnitude: 10–30 µJ per transition at 168 MHz / 3.3 V. Values outside 5–60 µJ suggest a firmware issue (e.g., the chip is not actually entering Stop, or the RTC alarm is not the wake source). Total wall time: ~5 minutes including firmware build.
Output: per-byte communication energy reported; BLE/LoRa datasheet anchors computed; Stop-mode firmware path verified; PPK2 noise floor characterized for the session; wake-up energy E_wakeup measured and reported.
NIGHT RUN: Stop-mode validation measurement — `stop_validation.py` runs DUT in Stop mode for 1 hour, captures sub-µA trace. Per section 5.4.3, the result is reported as a point estimate or upper bound depending on SNR. Together with the wake-up energy burst measured during today's working hours, this is one of the two ground-truth measurements that anchor the sleep model (section 5.4.3). The 1-hour duration also doubles as a long-term PPK2 stability check.

### Day 10 — Stop-mode analysis, methodology lock, Week 2 review
Objective: analyze last night's Stop-mode data, compare to datasheet, lock the methodology before the production sweep begins.
- Compute mean current over the 1-hour Stop-mode trace.
- Compute the SNR ratio: measured_mean / empirical_PPK2_noise_floor (from yesterday's no-load capture).
- Apply the section 5.4.3 reporting rules: point estimate if SNR > 5×, upper bound only if 1× < SNR ≤ 5×.
- Compare to STM32F407 datasheet IDD_STOP at 3.3 V, 25 °C (typical 0.6 µA, max ~5 µA).
- Report measured-vs-datasheet relationship. If consistent, lock the model. If grossly inconsistent (>5× off datasheet typical), diagnose firmware or board issue before continuing.
- Write `analysis/sleep_model.py`: implement the formula given in section 5.4.3, namely E_total = E_active + (T_sleep × IDD_sleep × V) + (n_wakeups × E_wakeup). Inputs: the validated IDD_STOP (or upper bound) from today's analysis, the measured E_wakeup from Day 9, the active-mode energies from the production sweep cells. Output: a callable that returns E_total for any (N, duty-cycle) combination. Unit-test with two sanity points: at duty cycle = 100% the sleep and wake-up terms vanish; at duty cycle = 0% with no sessions the active term vanishes.
- Methodology lockdown: at this point, methodology document + analysis pipeline + variance N_reps + comm energy + datasheet-anchored channel values + sleep model are all in place. Tag methodology version `v1.0` in git.
- Send methodology document to external reviewer #1 with a 72-hour turnaround request.
- Week 2 retrospective in ``.
Output: methodology v1.0 tagged; external review #1 in flight; rig and analysis ready for production sweep.
NIGHT RUN: if external review feedback can be incorporated quickly, the night can run the BN254 Mode A pilot for N=1 (smallest cell). If methodology is in flux, leave the rig idle. Conservative default: BN254 Mode A N=1 only, ~5 minutes; saves time on Day 11 morning.

## Week 3 — Pilot Sweep (BN254 Production)
### Day 11 — Incorporate review #1 feedback, BN254 Mode A small N
Objective: address external review feedback, then run the BN254 Mode A sweep for small N values.
- Read review #1. Categorize comments into: must-fix-before-production, fix-during-writeup, defer.
- Apply must-fix items to methodology document and code.
- Run BN254 / Mode A / N ∈ {1, 5, 10} / reps={N_reps} at 3.3 V. Cells run via `run_cell.py` in the sweep wrapper. Estimated wall time: with N_reps=5, ~9 cells × ~5 minutes per cell = ~1 hour.
- Sanity check: per-round energy decreases with N (consistent with H1).
Output: review #1 addressed; first 9 BN254 Mode A cells captured.
NIGHT RUN: BN254 Mode A medium N — N ∈ {25, 50}, 2 × N_reps cells. With N=50 taking ~25 seconds per rep × N_reps reps + cooldown, this is ~30 minutes total. Trivial overnight load.

### Day 12 — BN254 Mode A large N, BN254 Mode B
Objective: complete BN254 Mode A and run all of BN254 Mode B.
- Run BN254 / Mode A / N ∈ {100, 200} / reps={N_reps} at 3.3 V. ~40 minutes wall time.
- Run BN254 / Mode B / N ∈ {1, 3, 9, 30} / reps={N_reps}. ~20 minutes wall time.
- First analysis pass: load all BN254 cells, plot energy vs N for Mode A with Mode B as reference line, save as `report/figures/bn254_energy_vs_n_draft.pdf`.
- Sanity check: AmorE curve decreases monotonically; direct line is approximately flat.
Output: all BN254 cells captured at 3.3 V; first energy-vs-N draft figure generated.
NIGHT RUN: BLS12-381 Mode A small N — N ∈ {1, 5, 10}, N_reps reps each. With BLS taking ~5× the BN254 time, this is ~3 cells × ~30 minutes per rep × N_reps + cooldowns ≈ 6–8 hours overnight. Confirms BLS pipeline before tomorrow.

### Day 13 — BLS12-381 Mode A medium N (kickoff)
Objective: verify the overnight BLS small-N data, then run medium-N during the day.
- Load and quick-check the overnight BLS small-N data. Per-round energy should be ~5× the BN254 figure.
- If anything is off (failed cells, suspicious traces), investigate before continuing.
- Run BLS12-381 / Mode A / N ∈ {25, 50} / reps={N_reps}. Expected wall time: ~6–8 hours, runs through the workday and possibly into evening.
Output: BLS small-N validated; medium-N captured.
NIGHT RUN: BLS12-381 Mode A large N — N=100 with N_reps reps. ~3–5 hours overnight.

### Day 14 — BLS12-381 Mode A largest N, BLS12-381 Mode B
Objective: complete BLS Mode A with N=200 and run all of BLS Mode B.
- Run BLS12-381 / Mode A / N=200 / reps={N_reps}. ~3–4 hours wall time.
- Run BLS12-381 / Mode B / N ∈ {1, 3, 9, 30} / reps={N_reps}. ~3 hours wall time.
- Quick-check trace alignment for the large-N AmorE cells; phase markers must be present in every trace.
- Begin checking spread per cell using `analysis/spread_check.py`. Flag cells with spread above the section 5.4.4 target half-width.
Output: all BLS cells at 3.3 V captured; spread report generated.
NIGHT RUN: high-spread re-runs from Week 3 — for any flagged cells, run additional reps to bring the confidence interval into target. This may run multiple cells in sequence; the watchdog/resume handles failures gracefully. Capacity: ~10 hours of additional reps possible overnight.

### Day 15 — Week 3 retrospective, voltage sub-sweep firmware prep
Objective: review the production data; prepare for voltage sub-sweep.
- Plot current best-estimate energy-vs-N for both curves, both modes, with confidence intervals.
- Check H1 (monotonicity) and H3 (BLS crossover lower than BN254) qualitatively.
- Identify any cells that still need additional reps.
- Update PPK2 voltage configuration to support 3.0 V. Verify STM32 boots and runs at 3.0 V — STM32F407 minimum supply is 1.8 V so this is well within spec, but verify the full firmware path executes correctly.
- Week 3 retrospective in ``.
Output: preliminary energy-vs-N curves generated for both curves; 3.0 V firmware path verified.
NIGHT RUN: voltage sub-sweep — BN254 / Mode A / N ∈ {10, 50, 100} / reps={N_reps} at 3.0 V. With cooldown, ~3 hours.

## Week 4 — Voltage Sub-Sweep, Gap-Filling, First Crossover Analysis
### Day 16 — Voltage sub-sweep BN254 Mode B, BLS Mode A
Objective: complete the BN254 voltage sub-sweep on Mode B; begin BLS on voltage.
- Quick-check overnight BN254 Mode A 3.0 V data.
- Run BN254 / Mode B / N ∈ {10, 50, 100} / 3.0 V / reps={N_reps}. ~1 hour.
- Compute first voltage sensitivity coefficient ∂E/∂V from the BN254 data. Document.
- Begin BLS12-381 / Mode A / N ∈ {10, 50, 100} / 3.0 V / reps={N_reps} during work hours.
Output: BN254 voltage sub-sweep complete; first ∂E/∂V coefficient reported.
NIGHT RUN: BLS12-381 voltage sub-sweep — continue Mode A, then Mode B / N ∈ {10, 50, 100} / 3.0 V. Total ~6–10 hours overnight.

### Day 17 — Crossover analysis: first pass
Objective: compute the crossover N* across all conditions in the data so far.
- Write `analysis/crossover_analysis.py`: for each curve and each duty cycle, compute the smallest N at which AmorE per-round energy is below direct-pairing per-round energy (extrapolated as constant times N for direct).
- Generate the crossover map in (N, duty-cycle) plane for both 3.3 V and 3.0 V, both curves.
- Compute the duty-cycle sensitivity coefficient ∂N*/∂(duty cycle).
- Sanity check: crossover behavior is consistent with H3 (BLS crossover at smaller N) or document why not.
- Write `analysis/comm_projection.py`: project the crossover to BLE-class and LoRa-class per-byte energy figures, using literature values.
Output: first complete crossover map generated; comm-channel projections computed.
NIGHT RUN: phase-resolved breakdown analysis — for each cell at the chosen "representative" N (e.g., N=50), compute energy per phase. This involves rewriting the parser slightly and running on all archived traces. Trivial compute, can be a fast night run.

### Day 18 — Phase breakdown analysis, sensitivity analysis
Objective: produce the phase breakdown figures and the sleep-model sensitivity analysis.
- Generate `report/figures/phase_breakdown_bn254.pdf` and `bls12_381.pdf`.
- Run sleep-model sensitivity: ±10% perturbation around the validated IDD_STOP value. Plot how the crossover map changes.
- Identify the duty-cycle regime where the conclusion is robust to ±10% sleep-current uncertainty. This is what you can claim with confidence; everything else is uncertain.
- Sanity check: sensitivity bounds do not flip the qualitative conclusion at any duty cycle in the central regime.
Output: phase breakdown figures and sensitivity analysis complete.
NIGHT RUN: fill any cells flagged with high spread or remaining gaps. The watchdog/resume design lets this be set up and forgotten.

### Day 19 — Voltage analysis, communication projection writeup
Objective: produce the voltage and communication analyses; begin drafting their report sections.
- Plot voltage sensitivity: how does the crossover N* shift between 3.3 V and 3.0 V? Report ∂N*/∂V coefficient with uncertainty.
- Sanity check: voltage shift is small relative to duty-cycle and channel sensitivity (consistent with H6) or document otherwise.
- Draft the communication-channel projection narrative for the report: "At UART (measured: X µJ/byte), AmorE wins for N > Y. Projecting to BLE (Z µJ/byte from literature), AmorE wins for N > W." etc.
- Sanity check: per-byte energies are ordered as expected (UART < BLE < LoRa typically, by an order of magnitude or two).
Output: voltage and channel projection analyses complete; first draft of relevant report sections written.
NIGHT RUN: any remaining gap-fill cells (spread re-runs, missing voltage cells, etc.) can run overnight.

### Day 20 — Week 4 retrospective; production data freeze
Objective: freeze the production data; everything from here is analysis and writing.
- Verify that all cells planned in section 5.4 have been measured at the locked N_reps.
- Verify that the spread for every cell is at or below the target. Document any that aren't and why.
- Tag the production data: git tag `data-v1.0` on the measurement repo.
- Week 4 retrospective in ``.
Output: production data frozen and tagged; all planned cells captured.
NIGHT RUN: regenerate every figure from the latest data using `make figures`. Confirms the make pipeline works end-to-end before report writing begins. ~30 minutes; not strictly an overnight job, but it's a good hand-off.

## Week 5 — Outlier Investigation, Re-runs, Polishing the Dataset
### Day 21 — Outlier review
Objective: look at every cell with elevated spread or unexpected results; decide whether to re-run, accept, or document.
- For each cell flagged in Day 20, examine the individual reps. Is one rep an outlier (e.g., a hardware glitch)? If so, drop it and document.
- Re-run any cells where re-measurement might tighten the CI to acceptable bounds.
- For cells where the spread cannot be tightened, document the reason in the methodology section.
Output: outlier review complete; flagged cells either re-run, dropped, or documented.
NIGHT RUN: re-run sweep — cells identified for re-measurement run overnight.

### Day 22 — Final dataset audit
Objective: produce an end-to-end audit of the dataset before the report is written.
- Generate a table: every cell × every metric (mean, σ, N_reps, CI half-width). One row per cell.
- Spot-check 10 random cells: re-derive the mean energy from the raw CSV by hand (one-liner script), compare to the table.
- Sanity check: hand-derived value matches the table within rounding.
- Save the audit table as `analysis/audit_table.csv` and as `report/figures/audit_table.pdf`.
Output: audit table complete; sanity-checked.
NIGHT RUN: this is largely a manual day. If anything new surfaced, schedule re-measurement now.

### Day 23 — Report outline and key figures
Objective: produce a complete report outline and lock the key figure set.
- Write a section-by-section outline in `report/outline.md`: each section gets one paragraph stating what claim it makes and what figure or table backs it.
- Lock the figure list (target: 5–7 figures): energy vs N (BN254), energy vs N (BLS12-381), crossover map (3.3 V), phase breakdown, voltage sensitivity, channel projection, sleep-model sensitivity.
- Generate final-quality versions of all figures with consistent styling, using a single shared `report/figures/style.mplstyle`.
- Sanity check: every figure is regenerable from `make figures`.
Output: report outline locked; all figures generated at final quality.

### Day 24 — Slack / contingency
Objective: absorb any slippage from earlier in the week.
- If on schedule: begin LaTeX setup (template, bibliography, draft of abstract).
- If behind: catch up on the Day 23 figure generation or the Day 22 audit.
- If ahead: read 2–3 recent papers from the IACR/CHES citation pool to refine the Related Work section.
Output: schedule realigned; LaTeX skeleton or related-work notes ready.

### Day 25 — Week 5 retrospective; report writing kicks off
Objective: begin the actual writing.
- Report skeleton: title, authors, abstract placeholder, all section headings, figure placeholders linked to `report/figures/`.
- Write Section 2 (Background) and Section 3 (Methodology); these are the most stable sections and can be drafted from the PRD with minor adaptations.
- Week 5 retrospective in ``.
Output: Background and Methodology sections drafted.

## Week 6 — Report Writing
### Day 26 — Results section
Objective: draft the Results section.
- Write Section 4 (Results): one subsection per research question (RQ1–RQ6).
- Each subsection states the question, presents the figure or table, reports the numerical answer with CI, and connects to the relevant hypothesis.
- Sanity check: every numerical claim in the section traces to a specific cell or set of cells in `analysis/audit_table.csv`.
Output: Results section drafted.

### Day 27 — Discussion section
Objective: draft the Discussion section.
- Write Section 5 (Discussion): consolidate the implications of the results.
- Subsections: when delegation wins (parameter regimes), when it loses, the role of communication channel choice, the role of voltage, limitations.
- Limitations subsection is honest: single-DUT sample, two-point voltage, modeled (not measured) Standby, single Cortex-M4 platform, single curve family.
- Sanity check: every claim about "these results show" is followed by a specific figure or number.
Output: Discussion section drafted.

### Day 28 — Introduction, Abstract, Conclusion
Objective: draft the bookend sections.
- Write Section 1 (Introduction): motivation, gap, contribution, paper structure.
- Write Section 6 (Conclusion): one paragraph each on what was found, what's next, and reproducibility.
- Write the abstract: ≤200 words, structured (background, method, finding, implication).
- Sanity check: read the abstract aloud. Does it stand on its own?
Output: complete first draft of the report.

### Day 29 — References, related work
Objective: complete the references and the related-work sub-section.
- Add at least 5 IACR/CHES citations 2018–2026 (acceptance criterion D1).
- Verify every citation has a working DOI or stable URL.
- Write the Related Work subsection (in the Introduction or as Section 1.x): how this study relates to existing pairing-on-MCU benchmarks and existing energy benchmarks of crypto primitives.
- Run `bibtex` cleanly with no warnings.
Output: references complete; related-work subsection drafted.

### Day 30 — Reproducibility statement; repo cleanup; Week 6 retrospective
Objective: draft the reproducibility statement and clean up the repository.
- Write the Reproducibility Statement section: list of artifacts, where they are, how to reproduce.
- Clean up the repo: remove stale scripts, dead notebooks, debug prints, half-finished experiments. Tag exploratory notebooks as such.
- Update the README with the final reproduction walkthrough.
- Run the README walkthrough on a fresh clone of the repo (use a temp directory). Confirm `make figures` works.
- Week 6 retrospective in ``.
Output: reproducibility statement drafted; repo clean.

## Week 7 — External Review #2, Polish, Release
### Day 31 — External review #2 dispatch; first polish pass
Objective: send the report draft to external reviewer #2 with 72-hour turnaround; while waiting, polish.
- Send `report/main.pdf` and the repo URL to reviewer #2 with explicit prompts: "please flag (a) any claim not backed by a figure, (b) any methodology weakness, (c) any prose issue."
- First polish pass: read the report end-to-end, fix typos, clarify awkward phrasing.
- Verify that every numerical claim has a CI.
Output: review #2 in flight; first polish pass complete.

### Day 32 — Polish pass 2; figure tuning
Objective: second polish pass; tune figures for publication quality.
- Second polish pass: tighten language; remove redundancy.
- Figure quality pass: consistent fonts, legible at print size, color-blind-friendly palette, descriptive captions.
- Verify abstract reflects the actual findings, not the hypotheses.
Output: polish complete.

### Day 33 — Incorporate review #2 feedback
Objective: review #2 returns; incorporate.
- Read review #2. Categorize comments: must-fix-before-release, fix-as-future-work, defer-to-v1.1.
- Apply all must-fix items.
- Document deferred items in `docs/future_work.md`.
- Final pass on the report; final pass on the README; final pass on ``.
Output: review #2 incorporated; report at release quality.

### Day 34 — Slack day before release
Objective: absorb any unfinished items from earlier in the week before release.
- If anything is still incomplete, finish it.
- If everything is done, dry-run the release process: tag a candidate `v1.0-rc1`, walk through the release notes, verify GitHub renders the README correctly.
- Triple-check the report PDF: page count, figure quality, abstract, references.
Output: release candidate tested.

### Day 35 — Public release and outreach email
Objective: tag the public release on GitHub, post the report PDF, and send the outreach email drafted from actual findings.
- Tag the repo: `v1.0`. Push tag and release notes to GitHub.
- Upload the report PDF as a release asset.
- Final sanity check: open the GitHub release page in an incognito browser; click the report link; confirm it works.
- Draft the outreach email in `docs/outreach_draft.md` from actual findings, per the policy in section 7.5. Apply the acceptance criteria from 7.5 as a checklist before sending. If results are null or negative, the email reports that; the work is the artifact, not the desired finding.
- Send the outreach email to the academic correspondent.
- Project retrospective: 1-page diary entry covering what went well, what went wrong, what to do differently.
Output: public release; outreach email drafted from findings and sent; project closed.

## 7.4 Why the schedule fits: night-run accounting
The 35-day schedule is feasible only because physical measurement runs unattended overnight. The cumulative night-run capacity over the 7 weeks is approximately:
- Week 1 (Days 4–5). Stability and pre-measurement scaffolding. ~12 hours of unattended measurement.
- Week 2 (Days 6–10). Idle drift, variance characterization, communication isolation, Stop-mode validation, partial pilot. ~50 hours of unattended measurement.
- Week 3 (Days 11–15). BN254 and BLS production sweeps; voltage sub-sweep starts. ~50 hours of unattended measurement.
- Week 4 (Days 16–20). Voltage sub-sweep completes; gap-fill; analysis night runs. ~30 hours of unattended measurement.
- Week 5 (Day 21). Outlier re-runs. ~10 hours of unattended measurement.
Nominal night-run capacity: approximately 150 hours over 7 weeks. The watchdog/resume design ensures that overnight failures do not waste the next day's work.
Realism caveat (added in v3.1): the nominal figure assumes successful unattended operation. Empirically, USB-suspend, host-laptop sleep, nRF Connect crashes, ST-Link disconnects, and PPK2 driver issues cause approximately 30–50% of night runs to fail or partially fail during the first weeks of any new measurement setup, even with watchdog logic. The realistic effective capacity is therefore approximately 80–100 hours, of which 50–70 are needed for the planned cells, leaving 10–50 hours of true slack for re-runs and contingency. The Day 6 acceptance criterion (12-hour unattended run completes cleanly before declaring the watchdog ready) is the gate that elevates the effective capacity into the upper end of that range. If Day 6 reveals that the rig cannot reliably complete a 12-hour run, the production sweep is delayed until reliability is established, even at the cost of pushing the schedule by 2–3 days.
## 7.5 Outreach email — policy deferred to week 7 (TBD)
Earlier versions of this PRD (v3.0, v3.1, v3.2) attempted to fully specify the outreach email — template, closing, drafting procedure — before any measurement had been performed. External review across two rounds surfaced two problems with this approach: (a) drafting before findings exist produces hopes-as-text, which leaks into the tone of whatever the actual data turns out to support; (b) v3.2 had unresolved internal contradictions across sections 7.5.1, 7.5.2, 7.5.3, and the D3 acceptance criteria regarding whether offering a call is acceptable.
v3.3 resolves both by deferring the email policy to week 7. The draft, the framing, the closing form, and the timing decision are all made on Day 33–35, against the actual findings, in a separate document (`docs/outreach_draft.md`) — not in this PRD. The acceptance criteria below are the minimal stable set that holds regardless of what week 7 decides.
### 7.5.1 Acceptance criteria for D3 (stable, not deferred)
- Length. Under 200 words including signature.
- Links. Contains a working link to the public repository and to the report PDF, tested in an incognito browser before send.
- Findings. Names at least one specific result (number, ratio, parameter regime) that the recipient does not already have. Honest about null or negative findings.
- No employment statements. Does NOT state the author's employment situation, request a job, or include phrases of the form "no expectation," "would help me a lot," "if anything fits." The cold artifact-share email is about the work; the employment conversation, if one happens, is in a follow-up exchange that the recipient initiates.
- No retrospective justifications. Does not explain why the project happened. "The work shows X" is stronger than "I did this because Y."
- No effort quantification. Does not state how long the project took, how many cells were measured, or whether it was difficult.
- No hedging on flagged findings. If a result requires hedging ("appears to," "may suggest"), it is omitted from the email and stays in the report.
### 7.5.2 Deferred to week 7 (TBD)
- Closing form. Whether the email offers a call, points to a specific artifact for the recipient to engage with, asks a specific question, or simply ends — to be decided in week 7 against the actual findings and the author's situation at that time.
- Subject line. Specific phrasing — to be decided in week 7 once the headline finding is known.
- Opening line. How to reference the prior PR #317 thread — to be decided in week 7.
- Drafting procedure. Number of variants, review process, send timing — to be decided in week 7. The PRD does not pre-commit to a procedure that may not match the situation.
### 7.5.3 What week 7 should produce
By end of Day 35, `docs/outreach_draft.md` contains the final email and a brief decision record (1–2 paragraphs) of why each TBD item from 7.5.2 was resolved the way it was. The acceptance criteria in 7.5.1 are checked against the draft before sending. The send action itself is logged in ``.
# 8. Budget
The hardware budget is small. The costs below are the all-in cost of items not already in the author's possession.

The instrument is reusable for any subsequent embedded energy work and retains a substantial fraction of its purchase value on the second-hand market, so the marginal cost specifically attributable to this study is lower than the table suggests. The spare Discovery board is added in v3.0 as a risk mitigation against board damage during solder-bridge modification (Day 3); if the modification succeeds without incident, the spare remains available for follow-on work.
# 9. Risks and Mitigations

# 10. Success Criteria
The study is considered successful if all of the following hold at the end of week seven:
- S1. The full test matrix (including voltage sub-sweep, communication isolation, and Stop-mode validation) has been measured at the variance-determined repetition count, or the deviation is documented in the report with its reason and impact.
- S2. Energy per round is reported with confidence intervals tight enough that the existence or non-existence of a crossover N* can be claimed without overreach. If the data does not support a clean claim, the report says so.
- S3. The crossover map (where applicable) is published with all underlying data; the methodology is reproducible from the public repository by an external party; the channel projection extends the result to BLE-class and LoRa-class energy figures.
- S4. The technical report is at arXiv preprint quality, with at least 5 figures, an external review incorporated, and a tight abstract.
- S5. The outreach email is sent meeting the stable acceptance criteria in section 7.5.1: drafted from actual findings, under 200 words, with working links to repo and report, naming at least one specific finding, and without statements about the author's employment situation. The closing form and other deferred items in 7.5.2 are resolved on Day 33–35 and recorded in `docs/outreach_draft.md`.
The study does not require a positive finding to be successful. A clear, well-supported negative result meets all five criteria. Honest scope and clean methodology are the success conditions; the direction of the result is not.

# Appendix A. Bill of Materials

# Appendix B. Wiring Schematic
The wiring required for measurements is summarized below. All references to specific solder bridges or jumpers (e.g., SB1, JP1) are based on the standard STM32F407G-DISC1 layout and must be verified against UM1472 for the specific board revision in hand before any modification. This verification is the first activity on Day 1 of the schedule.
Power supply (measured by PPK2)
PPK2 VOUT  ----jumper, F-F---->  STM32 3V3 rail (e.g., JP1 pin 2)
PPK2 GND   ----jumper, F-F---->  STM32 GND
                                  [SB1 cut to disconnect on-board LDO]
Event-trigger lines (digital)
PPK2 GPI 1 ----jumper, M-F---->  STM32 PA0 (Setup, Blind, Verify boundaries)
PPK2 GPI 2 ----jumper, M-F---->  STM32 PA1 (ServerWait boundaries)
PPK2 GPI 3 ----jumper, M-F---->  STM32  (Mode C UART isolation triggers)
Communication (UART to server)
STM32 UART TX ---jumper-------->  Pi 3B GPIO RX (pin 10)
STM32 UART RX ---jumper-------->  Pi 3B GPIO TX (pin 8)
STM32 GND     ---jumper-------->  Pi 3B GND
Host connections (USB; not measured)
Host laptop  ---USB-A to Micro-B---->  PPK2 (USB DATA/POWER port)
Host laptop  ---USB-A to Micro-B---->  STM32 ST-Link USB (debug only)
During measurement, the STM32 is powered exclusively from the PPK2's VOUT line (via the 3V3 rail with the on-board LDO disconnected); the ST-Link USB cable provides only debug and program access, not power. This isolation is essential to ensure that all current drawn by the STM32 application processor is observed by the PPK2.
VBAT pin: not used. VBAT is the RTC backup-domain supply on STM32F407 and provides only ~3 µA of standing draw to maintain the RTC during main-supply outage. It cannot power the application processor and is intentionally left to its default (typically tied to VDD on the Discovery board).
# Appendix C. References
(References to be finalized at writeup time. Day 29 includes a verification pass.)
- Authors of the AmorE protocol. "AmorE: Amortized Pairing-Delegation" (full citation pending).
- Aranha et al. RELIC Toolkit. https://github.com/relic-toolkit/relic
- Nordic Semiconductor. "Power Profiler Kit II User Guide." https://docs.nordicsemi.com/
- STMicroelectronics. UM1472 — Discovery kit with STM32F407VG MCU User Manual, current revision.
- STMicroelectronics. STM32F407 Datasheet, current revision.
- Author. "AmorE on Cortex-M4" (BN254 and BLS12-381 ports). Public GitHub repositories, 2026.
- Survey of pairing-based cryptography on embedded platforms (full citation list at writeup; minimum 5 IACR/CHES citations 2018–2026 per acceptance criterion D1).
- Per-byte energy figures for BLE and LoRa (channel-projection literature, to be cited at writeup).
# Appendix D. Glossary of Project-Specific Terms
These definitions are normative for the project. Code, scripts, file names, and all written artifacts use these terms consistently.
- Cell. A single (curve, protocol, N, voltage) tuple in the test matrix. Example: (BLS12-381, AmorE, N=50, V=3.3V) is one cell. A cell is the unit of measurement.
- Round. One iteration of the AmorE protocol's blind/server-wait/verify cycle (Mode A), or one direct pairing (Mode B).
- Phase. A measurable subdivision of a round. Phases for Mode A: Setup (once per cell), Blind, ServerWait, Verify, Summary. For Mode B: PairingI for i in 1..N. For Mode C: UART_TX_K or UART_RX_K. Each phase is delimited by GPIO trigger transitions per section 5.2.1.
- OneTimeSetup. The protocol's initialization phase, executed once per session (cell) and amortized across all rounds in the cell. Distinct from per-round Setup.
- Trace. A time-series of (timestamp, current_mA, voltage_V, GPIO_state) records exported from the PPK2 for a single cell. Stored as CSV, named according to the convention `<curve>_<mode>_N<n>_V<v>_rep<r>.csv`.
- DUT. Device Under Test. In this project, the DUT is the STM32F407G-DISC1's STM32F407 application processor, powered through the 3V3 rail with the on-board LDO disconnected.
- Cell sweep. The act of running every cell in the test matrix once.
- Pilot sweep. A sweep limited to one curve (BN254), used to validate the methodology before scaling to the full matrix. Week 3 of the schedule.
- Production sweep. The full matrix sweep that produces the final dataset. Weeks 3–5 of the schedule.
- Spread. The relative range of energy values across the repetitions of a single cell, computed as (max - min) / mean. Cells exceeding the variance-driven target are flagged for additional repetitions.
- N_reps. The number of repetitions per cell, determined by the variance characterization on Day 7 per section 5.4.4.
- Watchdog/resume. The `run_cell.py` capability of detecting a hung or failed cell, recovering, and continuing the sweep without operator intervention. Specified on Day 6.
# Appendix E. Repository Layout
The public repository follows the directory structure below. New files generated during the project must be placed in the directory whose purpose matches their content. Automation tools and contributors rely on this structure for predictable file locations.
amore-energy-study/
├── firmware/
│   ├── stm32-amore/         # Mode A firmware (instrumented AmorE client)
│   ├── stm32-direct/        # Mode B firmware (direct N-pairing loop)
│   ├── stm32-uart-iso/      # Mode C firmware (UART isolation, new in v3.0)
│   ├── stm32-stop-test/     # Stop-mode validation firmware (new in v3.0)
│   └── shared/              # GPIO trigger lib, RELIC build config
├── server/
│   └── pi-amore-server/     # Raspberry Pi reference server
├── measurement/
│   ├── ppk2-control/        # Python automation (run_cell.py with watchdog/resume)
│   ├── traces/              # Raw CSVs (gitignored if total > 100 MB)
│   │   ├── variance_study/  # 
│   │   ├── comm_isolation/  # Mode C cells (new in v3.0)
│   │   └── stop_validation/ # 
│   └── calibration-logs/    # Per-session JSON calibration records
├── analysis/
│   ├── parse_traces.py
│   ├── compute_energy.py
│   ├── variance_summary.py  # new in v3.0
│   ├── comm_energy_fit.py   # new in v3.0
│   ├── sleep_model.py       # new in v3.0
│   ├── plot_energy_vs_n.py
│   ├── crossover_analysis.py
│   ├── comm_projection.py   # new in v3.0
│   ├── voltage_sensitivity.py # new in v3.0
│   ├── phase_breakdown.py
│   ├── spread_check.py
│   ├── audit_table.py       # new in v3.0
│   └── notebooks/           # Exploratory analysis only; not in publication path
├── report/
│   ├── main.tex             # Source
│   ├── figures/             # Generated by analysis scripts
│   ├── style.mplstyle       # Shared figure styling (new in v3.0)
│   └── refs.bib
├── docs/
│   ├── BOM.md               # Bill of materials
│   ├── wiring-schematic.png # Generated wiring diagram
│   ├── methodology.md       # Detailed methodology document
│   ├── board-modification/  # Photos and step-by-step
│   



├── Makefile                 # 'make figures' regenerates everything
├── LICENSE
└── README.md                # Reproduction walkthrough
Notes on the layout:
- `measurement/traces/` may exceed git's practical size limits. If so, traces are hosted externally (e.g., Zenodo) with a manifest in the repo.
- `analysis/notebooks/` is exploratory; nothing in the published report depends on a notebook running. Notebooks are committed for transparency, not for reproduction.
- `` is the chronological project diary; it 
# Appendix F. Decision Log (new in v3.1)
This appendix records non-obvious choices made during planning and execution, so they can be defended without re-derivation. The log is updated as decisions are made; entries below are seed entries from the planning phase.


# Appendix F. Decision Log (new in v3.1)
This appendix records non-obvious design decisions and the alternatives considered. Reviewers and future readers (including the author) will inevitably ask "why X and not Y?" — this log answers without re-deriving.
Each entry has the same shape: the question, the decision, the alternatives considered, and the reason. Entries are added during the project as decisions are made, not retrofitted at the end.
## F.1 Pre-execution decisions (locked in v3.1)
Why PPK2, not Otii Arc Pro?
Decision: PPK2. Alternatives considered: Otii Arc Pro, Joulescope JS220, INA228 + DMM, custom shunt + oscilloscope.
Reason: PPK2 has 25× the sample rate of Otii Arc Pro (100 ksps vs 4 ksps), which is more relevant than absolute accuracy for phase-resolved attribution. PPK2 noise floor (~200 nA) is acceptable for active-mode currents (50–200 mA). Cost is ~13× lower. Joulescope was rejected for cost. INA228 + DMM was rejected for sample rate. Custom shunt was rejected for development time.
Why 7 weeks, not 5 or 10?
Decision: 7 weeks. Alternatives considered: 3, 5, 10.
Reason: 3 was rejected for compressing the methodology phase below the quality bar (per PRD v1 → v2 revision). 5 was rejected as not allowing the three quality-critical additions identified in v3.0 review (communication-energy modeling, sleep validation, voltage variation). 10 was rejected as exceeding the author's financial runway and pushing the outreach window past the optimal point relative to PR #317. 7 is the minimum that allows all three additions.
Why 100 reps for variance characterization, not 50 or 200?
Decision: 100 reps. Alternatives considered: 30, 50, 200, 500.
Reason: 30 is the standard rule-of-thumb for normal-distribution σ estimation but gives wide CI on the σ itself. 50 is borderline. 100 is the smallest count that gives a tight estimate of σ across the full distribution including any heavy tails. 200 was considered but doubles the night-run time without commensurate improvement. 500 was rejected as wasted measurement.
Why 3.0 V (and not 2.85 V or full sweep) for voltage sub-sweep?
Decision: 3.0 V on a sub-sweep. Alternatives considered: 2.85 V, full sweep at 3.3 / 3.15 / 3.0 / 2.85, no voltage sweep at all.
Reason: 2.85 V is below STM32F407's specified minimum supply for some peripherals (datasheet) and would require firmware adjustments. 3.0 V represents a partially-discharged battery without entering spec-margin territory. Full sweep was rejected as exceeding the schedule budget for a sensitivity-only result. "No sweep" was rejected because reviewers will ask the question.
Why UART, not SPI, as the comm baseline?
Decision: UART. Alternatives considered: SPI, USB-CDC, raw GPIO bit-banging.
Reason: UART matches the existing AmorE port implementation and the existing prior project (PR #317 reference application). SPI would require a full firmware rewrite of the server-communication path and a corresponding rewrite on the Pi side. The science question is about per-byte energy as a proxy for an arbitrary channel, not about UART specifically; the channel projection (RQ5) handles the generalization.
Why measure Stop mode but not Standby?
Decision: measure Stop, model Standby from datasheet. Alternatives considered: measure both, measure neither, measure only Standby.
Reason: Stop is the more relevant mode for typical IoT duty cycles in the 0.1%–10% range. Standby is relevant only at extremely low duty cycles (< 0.01%). Measuring both adds 2 days for a result that affects only the extreme tail of the duty-cycle parameter space, which is reported with sensitivity bounds anyway. Standby is anchored to datasheet only, with explicit ±10% sensitivity.
Why N_reps determined by variance study, not fixed?
Decision: variance-determined N_reps. Alternatives considered: fixed N_reps = 3, fixed = 5, fixed = 10.
Reason: any fixed N_reps is either a guess or an unjustifiable claim. Variance-determined N_reps is the standard approach for measurement studies in conference-grade work. The cost is one Day 7 night run and a 30-minute analysis on Day 8. Fixed N_reps = 10 was the closest alternative but wastes measurement time on cells where variance is small, and would still be a guess.
Why a separate spare DUT (180 DKK)?
Decision: budget includes a spare. Alternatives considered: skip the spare and assume the primary survives.
Reason: Day 3 cuts a solder bridge, an irreversible action. The spare provides (a) a rehearsal target on Day 2 to refine the procedure before touching the primary, and (b) a fallback if the primary is damaged. The 180 DKK cost is small relative to the value of avoiding a multi-day delay if the primary fails. The spare retains value after the project as a continuing development board.
Why an arXiv preprint quality target, not a CHES submission?
Decision: arXiv preprint. Alternatives considered: CHES submission, USENIX submission, technical report only.
Reason: CHES/USENIX submission requires statistical power, threat-model rigor, and per-platform breadth that 7 weeks does not allow honestly. arXiv preprint quality is the highest target reachable in 7 weeks while maintaining honesty in claims. Technical-report-only undersells the work; the report's reproducibility, methodology, and analysis are at preprint-quality, and arXiv is the natural venue. Future submission to a workshop or conference is possible but not promised in the schedule.



Why TBD the email closing form rather than specify it now?
Decision: closing form is TBD, decided in week 7 from actual findings. Alternatives considered: specify offer-of-call, specify no-call-only, specify artifact-pointer-only, specify generic-availability.
Reason: each closing form has different optimal conditions, and those conditions depend on the actual findings. A clear positive crossover finding might warrant offering a 15-minute call (the recipient has incentive to engage). A null or negative finding might warrant no call (the email exists to share data, not to seek dialog). An ambiguous finding might warrant pointing to a specific artifact in the repo. Locking the form now would either commit to a closing that does not match the data, or carry the same internal contradiction across three sections that v3.2 had. Deferring to week 7 keeps the decision honest, at the cost of one more PRD revision in week 7. The cost is acceptable; the decision is consequential.
Why measure per-transition wake-up energy E_wakeup, not model it from datasheet? (added in v3.3)
Decision: measure E_wakeup directly via a 100-cycle Stop→Run→Stop burst on Day 9. Alternatives considered: ignore wake-up energy (assume zero), model from datasheet (T_wakeup × I_run typical), full per-mode characterization across voltage and temperature.
Reason: ignoring wake-up energy underestimates duty-cycle costs at high wake frequencies (relevant for IoT scenarios from 10 Hz down to 0.001 Hz, which is the central regime of this study). Datasheet modeling is the natural fallback but is poorly constrained — STM32F407 specifies wake-up time as a maximum (~13 µs from Stop), not a typical, and the ratio between max and typical can be 2–3× on this part. The 100-cycle burst is 5 minutes of measurement and produces a single hard number with ±5–10% uncertainty (set by PPK2 active-range accuracy and 100-cycle averaging). This converts the most uncertain term in the v3.0 sleep model into the second-most-certain (after IDD_STOP, which has a directly measured value too). Full per-mode characterization across voltage and temperature was considered but rejected as exceeding the scope; the primary 3.3 V / 25 °C measurement is anchored, with the 3.0 V sub-sweep providing a secondary point.
## F.2 Decisions made during execution
Add entries here as decisions are made during the 35-day execution. Each entry follows the F.1 template: question, decision, alternatives, reason.

End of document.

| Component | Role | Specification |
|---|---|---|
| Nordic PPK2 | Power source and current/voltage probe for the device under test | 0.8–5 V output, 200 nA – 1 A range, 100 ksps sampling, 8 digital input channels |
| STM32F407G-DISC1 | Device under test (DUT); runs AmorE client or direct-pairing test harness | Cortex-M4 at 168 MHz, 192 KB SRAM, 1 MB Flash |
| Raspberry Pi 3B | Untrusted server in the AmorE protocol; not measured | Quad-core ARM Cortex-A53; runs the AmorE server-side reference |
| Host laptop | Runs nRF Connect Power Profiler app, analysis scripts, and IDE | Linux or macOS; Python 3.10+ |


| Variable | Values | Count |
|---|---|---|
| Curve | BN254, BLS12-381 | 2 |
| Protocol | AmorE (Mode A), Direct (Mode B) | 2 |
| N (AmorE) | 1, 5, 10, 25, 50, 100, 200 | 7 |
| N (Direct) | 1, 3, 9, 30 (subset for cost reasons) | 4 |
| Voltage sub-sweep (new) | 3.0 V on {N=10, N=50, N=100} for both curves and both modes | 12 |
| UART payload (Mode C) | 16, 64, 256, 1024 bytes (TX and RX) | 8 |
| Stop-mode validation | Single 1-hour measurement on DUT in Stop mode | 1 |
| Repetitions per cell | Determined by variance study (section 5.4.4); typically 3–10 | 3–10 |
| Duty-cycle scenarios | Modeled in post-processing (5 levels: 100%, 10%, 1%, 0.1%, 0.01%) | 5 |


| Week | Phase | Activities |
|---|---|---|
| 1 | Equipment, board prep, firmware instrumentation | Verify UM1472; modify board (SB1, JP1); develop GPIO trigger firmware (Modes A, B, C); pilot measurement to validate the full pipeline. |
| 2 | Methodology lock-in: variance, calibration, Stop validation, comm energy | PPK2 automation with watchdog/resume; calibration procedures; variance characterization (Day 7 night run); Stop-mode validation; UART per-byte energy isolation. End-of-week external review #1. |
| 3 | Pilot sweep (BN254 production) | Full BN254 sweep (Mode A and Mode B) at 3.3 V with the variance-determined repetition count. First end-to-end analysis pipeline run on real production data. |
| 4 | Production sweep (BLS12-381 at 3.3 V) | Execute the BLS12-381 cells at 3.3 V; heavy use of overnight unattended runs. Begin voltage sub-sweep preparation. |
| 5 | Voltage sub-sweep, high-spread re-runs, gap-filling | 3.0 V cells for both curves; re-runs of any cells with spread above target; cleanup runs identified during analysis. |
| 6 | Analysis and figure generation | Phase-resolved breakdowns; crossover analysis; communication-channel projection; voltage sensitivity; sensitivity analysis; figure and table generation; preliminary draft of report. |
| 7 | Writeup, external review #2, reproducibility, release | Final report writing; repository cleanup; reproducibility verification by walking the README from scratch; external review #2 with 72-hour turnaround; public release. |


| Item | Vendor | Cost (DKK) |
|---|---|---|
| Nordic Power Profiler Kit 2 (PPK2) | DigiKey Germany / RS Denmark | ~680 DKK |
| USB-A to Micro-B cables (1 m, ×2) | DigiKey Germany or local store | ~20 DKK |
| USB hub, 4-port, USB 2.0 | Local Danish electronics store | ~60 DKK |
| Dupont jumper wires (assorted, 40-pack) | Local store or RS Denmark | ~50 DKK |
| Spare STM32F407G-DISC1 (risk mitigation) | DigiKey / Mouser | ~180 DKK |
| Shipping | — | ~50 DKK |
| Total estimated |  | ~1,040 DKK (~140 EUR) |


| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Equipment shipping delay | Low | Medium | Order from primary vendor today; identified two backup vendors. Schedule has slack in week 1. |
| UM1472 verification reveals different SB/JP identifiers than assumed | Medium | Low | Day 1 is allocated to verification before any modification. |
| Board damage from solder-bridge modification | Low | High | Photograph before/after; spare DUT included in budget; alternative measurement topology (USB pass-through) documented as fallback. |
| Variance characterization shows large σ requiring N_reps > 10 | Low | High | Root-cause investigation on Day 8 (USB instability, thermal coupling, host laptop sleep). Schedule has Days 9–10 slack absorbing methodology fixes. |
| Stop-mode measurement diverges from datasheet > 30% | Low | Medium | Widen sensitivity bounds, document. Does not block the study; weakens the duty-cycle conclusions at extreme low duty cycles only. |
| No crossover observed (H3 not supported) | Low | Low | Negative result is in scope; the email and report are reframed accordingly. The communication-channel projection (RQ5) is independent of the crossover claim. |
| Confidence intervals too wide to claim a crossover | Medium | High | Variance-driven N_reps; Days 14, 18, 19, 21 have re-run capacity; if still wide, document as a limitation honestly. |
| Hardware fault during a measurement session | Low | Medium | Per-session calibration catches drift; spare cables, spare DUT, watchdog/resume mitigates partial loss. |
| Schedule slippage beyond 1 week | Medium | Medium | End-of-week retrospectives surface slippage early; first cuts: voltage sub-sweep cells, then phase-breakdown depth, then communication-channel projection breadth. |
| Confounding from thermal effects in long sweeps | Medium | Medium | 5-second cooldown between cells; randomize cell order within each session; thermistor reading logged via STM32 ADC if available. |
| Software automation bug skews timing alignment | Low | High | Day 4 pilot pass with manual verification; sanity-check trace alignment against expected wall-clock duration before each major sweep. |
| External reviewer unavailable on requested timeline | Medium | Medium | Identified two reviewer candidates in advance; if neither responds, the report is reviewed by a careful self-pass against the Day 32 polish checklist. |
| Night-run failures from USB suspend, host sleep, nRF Connect crash, ST-Link disconnect (new in v3.1) | High | Medium | Day 6 host-stability configuration (caffeinate / systemd-inhibit, USB autosuspend disabled, udev rules); 12-hour unattended-run acceptance criterion before production sweep begins; watchdog/resume in run_cell.py keeps partial sweeps useful. |


| Item | Manufacturer P/N | Source | Quantity |
|---|---|---|---|
| Power Profiler Kit 2 | nRF-PPK2 (Nordic) | DigiKey DE | 1 |
| USB-A to Micro-B cable, 1 m | DH-20M50055 (Cvilux) | DigiKey DE | 2 |
| USB Hub, 4-port USB 2.0 | U222-004 (Tripp Lite) or local equiv. | DigiKey DE / Local | 1 |
| Dupont jumper wires | (generic, 40-pack) | RS DK / Local | 1 pack |
| STM32F407G-DISC1 (primary) | Already in possession | — | 1 |
| STM32F407G-DISC1 (spare; new in v3.0) | STM32F407G-DISC1 | DigiKey / Mouser | 1 |
| Raspberry Pi 3 Model B | Already in possession | — | 1 |
| ST-Link debugger | Built into Discovery board | — | — |


| Decision | Rationale |
|---|---|
| PPK2 over Otii Arc Pro | PPK2 sample rate (100 ksps) is 25× higher, relevant to phase-resolved attribution. ±5% accuracy in the 50–200 mA range is well below the inter-cell differences expected. Cost is ~13× lower. See section 6. |
| 100 reps for variance characterization | Sufficient to estimate σ to within ~10% relative uncertainty (since SE(σ) ≈ σ/√(2(n-1))), which is the precision needed to choose between N_reps={3, 5, 10}. 50 reps would estimate σ to ~14%, marginal; 200 reps would estimate to ~7%, beyond what the decision needs. Wall time ~3 hours is comfortably overnight. |
| 3.0 V (not 2.85 V) for voltage sub-sweep | STM32F407 is rated for 1.8–3.6 V, but several on-board peripherals on the Discovery board (ST-Link, audio DAC) are not characterized below 3.0 V. 3.0 V represents a partially-discharged 3.7 V Li-Ion battery, which is a realistic deployment scenario. 2.85 V approaches the lower spec edge for some peripherals and risks confounding measurement with peripheral instability. |
| UART (not SPI) as comm baseline | UART matches the existing AmorE-on-Cortex-M4 implementation; SPI would require firmware rewrite of both client and server, which is outside the scope and budget of this study. UART per-byte energy is the empirical anchor; channel projection extends to BLE/LoRa via per-byte multipliers. |
| N range {1, 5, 10, 25, 50, 100, 200} | Logarithmic spacing covers two orders of magnitude with 7 points, sufficient to characterize the asymptote and locate the crossover with sub-decade resolution. N=200 is the AmorE author's largest reported N; going beyond would extrapolate without a desktop reference. |
| Direct N values {1, 3, 9, 30} | Direct pairing energy is approximately constant in N (H2). Four points across two decades is sufficient to confirm linearity and detect any per-call overhead. More direct points would not change the AmorE-vs-direct comparison. |
| Modeled (not measured) Standby mode | Stop mode is measured directly (single ground-truth point on Day 9). Standby would require additional firmware work (RTC alarm wake-up, battery-backed RAM handling) that adds 1–2 weeks. The duty-cycle parameter range studied includes Stop but not deep Standby; the report's conclusions are bounded accordingly. |
| Random ordering within bins, not globally | Globally random orderings can interleave a 3-hour cell with a 1-second cell, creating thermal and drift confounds that defeat the purpose of randomization. Binning by run-time class preserves the bias-reducing property of randomization while avoiding the new bias. |
| arXiv preprint, not conference submission, as quality target | Conference-grade work (CHES/USENIX) requires more measurements (multiple devices, full voltage sweep, comprehensive sleep modes), longer methodology iteration, and external co-authorship for credibility. 7 weeks solo cannot produce that. arXiv preprint at high quality is achievable in 7 weeks and serves the practical purpose: a citable, reproducible artifact useful as a follow-up signal in professional conversations. |
