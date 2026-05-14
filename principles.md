# Principles · how and why this plan will work

This is the load-bearing doc. The plan, the dashboard, and the coach all derive from what's here. If anything in the system contradicts this, this wins.

---

## 1. The bet

The bet is small and specific: **a deconditioned 47-year-old with a wonky knee, three kids, and a full-time job, can run a sub-7-hour Two Oceans Ultra in April 2027 by training year-round at sub-threshold intensity — never harder — and never missing more than one session in any seven-day window.**

The age matters. Most "couch to ultra" plans were written for athletes in their 20s and 30s with intact tendons, a full night's sleep, and a calendar they own. None of that applies here. The Norwegian Singles Approach happens to be *better suited* to a 47-year-old than the periodized alternatives are — that's the argument that follows.

Everything downstream is in service of that one bet.

What we are *not* betting on: that you'll execute a perfectly-periodized plan with hill blocks, threshold blocks, race-pace blocks. That model works for athletes with healthy biomechanics and an open calendar. You are neither. The history of running plans for time-constrained adults is mostly a history of injury and unfinished mesocycles.

---

## 2. The mechanism — why sub-threshold every week, year-round

Sub-threshold work — the top of Zone 3, ~75–78% of heart rate reserve, lactate roughly 2–3 mmol/L — is the most adaptation-per-injury-risk you can buy.

What happens biologically at that intensity:

- **Mitochondrial density and capillarization increase** without significant inflammatory cost. The body builds aerobic plumbing.
- **Lactate clearance improves**. You're producing enough lactate to recruit the clearance machinery, but not enough to crush the legs.
- **Fast-twitch fibers stay metabolically engaged**. They're recruited at LT1+ but not flogged.
- **Recovery between sessions takes 24–36 hours, not 48–72**. This is what makes three quality sessions per week sustainable indefinitely — and at 47, the 48–72 hour recovery from threshold work would push you to one quality session per week, not three. The cumulative-adaptation math gets *worse* with age for periodized plans, *better* for NSA.

Compare that to threshold (Z4) work, the traditional "tempo" zone:

- Same aerobic adaptations on a per-session basis, *roughly*.
- 2–3× the recovery cost.
- Markedly higher injury and illness rates in deconditioned populations.
- You can do it once a week max. Sub-threshold you can do three times.

**Over a year, 156 sub-threshold sessions beats 52 threshold sessions on the metric that actually matters: cumulative time spent driving aerobic adaptation.** This is the Norwegian Singles bet, and the evidence base behind it — [Düking et al. 2021 meta-analysis on HRV-guided training][1], [Vesterinen 2016][2], the Ingebrigtsen lineage's published training logs (see [Running Writings' write-up of Kristoffer Ingebrigtsen's protocol][7]) — is the strongest in amateur-applicable endurance science.

The marketing-grade alternatives (80/20 polarized, Daniels VDOT-based plans, traditional periodization) work for people who can absorb the harder sessions. We are explicitly designing for someone who cannot.

---

## 3. Why year-round, not periodized

Periodization is a 1960s East German innovation, refined in the 1970s and 80s, and it works extremely well for athletes whose only job is training. The structure: build phase → intensity phase → peaking phase → taper → race → recovery → repeat.

That model has two assumptions that don't hold for you:

1. **You can absorb a high-intensity block without breaking.** Your knee history says no.
2. **Life lets you peak on a calendar.** It doesn't. Sleep gets shredded by a kid's ear infection, a Saturday gets eaten by something unmoveable, work has a release week. Periodization punishes missed sessions because each phase has dependencies on the prior phase.

A year-round constant-rhythm plan does the opposite. If you miss a Tuesday, you don't break a block — you just train Wednesday or skip. The shape of the week is the shape of the year. No phase. No peaking. No fragility.

The hill block in months 11–12 is the only periodized element, and that's because Constantia Nek demands quad-specific strength that flat sub-threshold won't develop. It's a four-week insurance dose, not a six-month peaking arc.

---

## 4. Why these specific data signals, and not others

The dashboard tracks five autonomic and load signals. Each one earns its place because it predicts *something the plan needs to know*, with evidence-backed thresholds.

### HRV (overnight rMSSD)
The single strongest individual-day predictor of training readiness. [Düking 2021 meta-analysis][1]: HRV-guided groups do fewer hard sessions and match or beat fixed plans on 3000m time. The [2025 Nature cyclist RCT][3] replicated. Caveat: no RCT shows direct injury reduction — the claim is performance + autonomic adaptation, not fewer injuries per se.

**What we do with it**: Garmin's own `HRV Status` (BALANCED / UNBALANCED / POOR) already encodes a personalized 60-day baseline. We use that directly. A 7-day weekly average dropping >3 ms versus prior week is an early-warning amber.

### Resting heart rate
Cheap, robust, complementary to HRV. RHR climbs before illness symptoms. RHR climbs during overreach.

**What we do with it**: today vs 14-day baseline. +3 amber, +5 red. These thresholds aren't arbitrary — they're standard in coaching literature (Selye/Friel) and roughly correspond to one standard deviation in normal populations.

### Sleep
Total sleep time is the only sleep metric that's reliably measured by a wrist device. Stage classification (deep/REM) is noisy versus polysomnography. We use the total only.

**What we do with it**: target 7.5h. Last night <6h is red. 7-day average <7h is amber. The science says sleep debt accumulates and one big night doesn't fully repay multiple short nights, so the 7-day window matters more than yesterday.

### Stress
Garmin's HRV-derived stress score is a black-box composite. Useful as a sanity-check signal, not a feature. Avg >50 during the day amber.

### Weight
Tracked because of the goal (75 kg from 86 kg), and as an overreach/illness proxy. Endurance training pulls weight down 0.2–0.4 kg/wk sustainably. Faster than that is a signal something is wrong — illness, under-fueling, or both. Flag drops >2 kg in 14 days as amber. **Don't punish gradual loss** — that's the goal.

We explicitly don't track:

- **TSB / "form" / Banister fitness-fatigue tapering oracle.** [Imbach et al. 2025][5] showed the fatigue parameters of the classic FFM are ill-conditioned for amateurs — they don't improve prediction beyond fitness alone. We use a smoothed load curve for ACWR; we don't trust the form/peak prediction.
- **Body Battery, Stress Score, Training Readiness as primary signals.** Derived metrics from HRV + activity. Use the inputs, not the composite.
- **VO2max, Race Predictor, Fitness Age.** Marketing-grade. The Race Predictor is famously optimistic for ultras and untrained athletes.
- **Sleep stages (deep/REM split).** Noisy versus PSG. Total only.

---

## 5. The traffic-light decision rule

Every morning the dashboard renders one of three states. The rule is intentionally simple — anything more complex erodes adherence.

| Signal | Amber if… | Red if… |
|---|---|---|
| HRV status | "unbalanced" | "poor" |
| HRV 7-day trend | dropped >3 ms in 7d | — |
| RHR vs baseline | +3 bpm | +5 bpm |
| Sleep last night | <7h | <6h |
| Sleep 7d avg | <7h | — |
| Weight drop | >2 kg in 14d | — |
| Stress avg | >50/100 | — |

**Any single red signal → red day**. Rest, mobility, walk if you want. No quality work, no gym, no long run.

**Any amber → easy only**. Half-volume Z2, skip gym. Save the prescribed quality for tomorrow.

**All clear → train as written**.

This is rigid on purpose. If the rules are fuzzy, you'll talk yourself into the hard session every time the data is borderline. The plan only works if you respect amber and red days. **Skipping a quality session because the data said so is a successful execution of the plan, not a failure.**

---

## 6. The Sunday rule

Every Sunday evening, answer five questions:

1. Slept 7+ hours per night on average?
2. Knee equal to or better than last Sunday?
3. Saturday's long run felt like I could have done more?
4. Hit both gym sessions?
5. Sub-threshold sessions felt controlled — not crushing?

**3+ yes → continue as planned. 2 or fewer → next week is automatically a down-week.** No ego, no negotiation, no "I'll just try to push through and see how it goes."

This is the safety valve for everything the daily traffic-light misses. Cumulative fatigue, life stress, accumulating tightness in a calf — none of those show up cleanly in HRV. The Sunday rule catches them.

---

## 7. The constraints we honor

These are facts about your life. The plan is designed around them, not against them.

- **Age 47**: tendons regenerate slower than they used to. Recovery from any single hard session takes longer. The principle this drives: **never two consecutive hard days, ever**. The plan never schedules them. The amber/red traffic light catches when even one hard day shouldn't have been hard. At 47 the cost of one missed Sunday rule is bigger than it was at 33.
- **Knee history**: every plan decision prefers caution over heroics. Hill work is dosed (every 3rd Saturday in build phases, not a full hill block). Treadmill is preferred over downhills in winter. Eccentric step-downs every gym session. Cadence target ≥170 spm — [the single highest-leverage intervention for knee load][4]; 5–10% above spontaneous cadence drops peak knee load by ~20%.
- **Full-time job, three kids**: total weekly volume tops out at ~12 hours in peak weeks. Most weeks 6–10 hours. Sessions are 45–90 minutes except the Sunday long run. Quality is on weekdays, the long run is Sunday morning. Three kids means sleep continuity is fragile — the rolling 7-day sleep average is more honest than any single night, and the Sunday rule's first question (slept 7+ hours per night on average?) is doing more work than you'd think.
- **Norwegian winter**: sub-threshold sessions can be 100% treadmill — pace control is the whole point of NSA, and the treadmill is *better* for that than icy outdoor work. Long runs go outdoor when conditions allow.
- **Weight 86 → 75 kg**: gradual weight loss is part of the plan, not separate from it. Lose ~0.25 kg/wk for 44 weeks. Don't fuel below the work — under-fueling tanks HRV within days, and at 47 it tanks tendon repair too.

---

## 8. What success looks like, week by week

Most people picture success as a race-day photo. That's the result. The process success looks like:

- **Showing up to four to six sessions a week.** Not "good sessions" — *any* sessions. Consistency is the metric.
- **Three of those sessions are quality (sub-threshold or hills), and you finished each feeling like you could have done another rep.** If you finished crushed, you went too hard.
- **The Saturday/Sunday long run finished, and you'd have walked any uphill the data said to walk.**
- **HRV stayed balanced more weeks than not.**
- **RHR baseline trended down across months, even if any given week was noisy.**
- **Weight trended down 1 kg/month, not 4 kg/month.**

If you do those things every week for 48 weeks, the Two Oceans Blue medal is essentially the byproduct. If you skip the Sunday rule and chase faster splits in week 12, you don't finish, or you don't start because you got hurt in week 18.

---

## 9. The most important sentence in this document

> The hard sessions show up as more reps and longer reps, not faster reps.

If you find yourself, six months from now, feeling unsporty during the Tuesday session because it's "not hard enough" — that's the plan working. Sub-threshold should feel boring. Boring is the goal. Boring is what makes the long run possible. Boring is what gets your knee to Cape Town.

Resist the urge to make it harder.

---

## 10. When to revisit these principles

Annually, after Two Oceans. Or:

- If three consecutive weeks come in red on the dashboard despite normal training and life — something in the principles needs adjusting (the model has stopped predicting your body).
- If you go four weeks straight in the green and feel under-stimulated — we might be capable of more volume, not more intensity. Add a 4th easy day before considering anything else.
- If injury recurs — the principles are right, the execution was wrong. Investigate that first.

The principles don't change because a single bad day happened. They change because the system's predictions stop matching reality across a long enough window.

---

*The plan is the plan. Trust the structure.*

---

## References

The plan and these principles cite specific studies. Each claim should be testable. If you want to push back on something here, push back through these:

[1]: https://pmc.ncbi.nlm.nih.gov/articles/PMC8507742/ "Düking et al. 2021 — HRV-guided endurance training meta-analysis (PMC8507742)"
[2]: https://pubmed.ncbi.nlm.nih.gov/26909534/ "Vesterinen 2016 — HRV-guided versus predetermined training prescription"
[3]: https://www.nature.com/articles/s41598-025-13540-z "2025 Nature — HRV-guided training RCT in cyclists, replicates Vesterinen"
[4]: https://pmc.ncbi.nlm.nih.gov/articles/PMC12440572/ "Anderson et al. 2025 — Running cadence and knee load systematic review"
[5]: https://www.nature.com/articles/s41598-025-88153-7 "Imbach et al. 2025 — statistical critique of the Banister fitness-fatigue model"
[6]: https://uphillathlete.com/aerobic-training/heart-rate-drift/ "Uphill Athlete — HR drift as aerobic fitness signal"
[7]: https://runningwritings.com/2025/07/kristoffer-ingebrigtsen-norwegian-single-threshold-training.html "Running Writings — Kristoffer Ingebrigtsen's Norwegian Singles protocol"

1. **[Düking et al. 2021 — Effects of HRV-Guided vs. Predetermined Block Training on Performance][1]**. Meta-analysis showing HRV-guided groups do fewer hard sessions yet match or beat fixed plans on 3000m and 10K. Foundation for the readiness traffic light.
2. **[Vesterinen 2016 — HRV-Guided Endurance Training][2]**. Doctoral dissertation. The methodology for using rMSSD trend as a training prescriber. Where the "weekly avg HRV down >3 ms" threshold originates.
3. **[Nature Scientific Reports 2025 — HRV-Guided Training in Cyclists][3]**. Recent RCT replicating Düking findings in trained cyclists with a 12-week protocol.
4. **[Anderson et al. 2025 — Running Cadence Modifications and Knee Load][4]**. Systematic review. 5–10% cadence increase = ~20% drop in peak knee adduction moment. This is why ≥170 spm is non-negotiable on the dashboard.
5. **[Imbach et al. 2025 — Statistical Flaws in the Banister Fitness-Fatigue Model][5]**. Why we use a smoothed acute/chronic load ratio but don't trust TSB as a tapering oracle.
6. **[Uphill Athlete — Aerobic Decoupling][6]**. Practical write-up of HR drift at steady pace as a personal aerobic-fitness signal. Caveat: the "<5% Pa:HR" threshold is Joe Friel's, not peer-reviewed — use as a personal trend.
7. **[Running Writings — Kristoffer Ingebrigtsen's Single Threshold Method][7]**. The protocol this plan is modeled on. Kristoffer ran 1:15 half marathon at 33 from overweight desk job in 18 months using exactly this approach.

### Where to push back

If something here looks like bro-science to you, the test is: can I find a peer-reviewed paper that says the opposite, or a coach with a published track record who disagrees? If yes, share it. If no, the principle stands.

The principles I'm *least confident in*, and where I'd be glad to be corrected:

- The HRV-guided literature is mostly trained cyclists and runners in their 20s–30s. Generalization to a 47-year-old reconditioning from couch is inference, not direct evidence. The direction probably holds — autonomic signaling doesn't fundamentally change with age — but the specific thresholds (-3 ms weekly trend, etc.) were calibrated on younger cohorts.
- The 0.25–0.5 kg/week weight-loss guideline for endurance athletes is consensus but not deeply RCT-validated.
- Cadence work is well-supported, but my 170 spm floor is arbitrary within the "natural ±5%" window — your spontaneous cadence might be 165, in which case 170 is fine; if it's 180 the floor should be higher.
