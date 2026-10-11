"""The definition of the various compost stages that exist.
duration_days = (min, most_likely, max), in days.
temp_range / moisture_range / o2_range / co2_range = (min, max) envelope.
nh3_relative = (min, max) on a 0.0-1.0 relative-intensity scale, NOT an
    absolute ppm - no source measures ppm-NH3 inside a small bin, only
    the timing pattern (rises with temperature, peaks at thermophilic
    peak, near zero by maturation). See note on MQ-135 below.
Sources for temp/moisture/duration: see header of previous version /
compost_measured_data.md [A]-[H].
Additional sources for gas data (compost_gas_data.md):
  [I] Cornell Waste Management Institute - compost.css.cornell.edu/physics.html
      O2/CO2 baseline: O2 starts 15-20%, anaerobic risk below ~5%.
  [J] Stegenta et al. 2019, Sustainability (MDPI 11(8):2340)
      CO2 pore concentration mostly 2-3%, isolated spikes >10%.
  [K] Pagans et al. 2006, ScienceDirect - NH3 rises exponentially
      during thermophilic (>45C), linearly during final mesophilic.
  [L] PSLab - pslab.io/measuring-co2-with-mq135 - MQ-135 is a SnO2
      sensor cross-sensitive to NH3/NOx/CO/CO2/VOCs, calibrated
      against 100ppm NH3 reference. It does NOT cleanly measure CO2.
IMPORTANT: the MQ-135 physically reports one composite, cross-sensitive,
drifting analog value - not a clean gas concentration. Turning
o2_range/co2_range/nh3_relative into a single simulated sensor reading
(with the documented 24-48h warm-up and per-unit R0 drift) is a
separate sensor-response step, done later, not encoded here.

Ambient at the Mataram pilot site is ~24-32C year-round. Stage 0 cannot
start below ambient and Stage 4 converges to it, so both are floored at
28C rather than the temperate-climate figures.
"""
STAGES = [
    {
        "id": 0,
        "name": "Early Mesophilic",
        "duration_days": (1, 2, 3),
        "temp_range": (28.0, 45.0),  # was 20.0 - pile can't start below ambient
        "moisture_range": (60.0, 65.0),
        "o2_range": (8.0, 15.0),
        "co2_range": (0.5, 3.0),
        "nh3_relative": (0.05, 0.25),
    },
    {
        "id": 1,
        "name": "Active Thermophilic",
        "duration_days": (2, 3, 4),  # was (2,2,3) - min==most_likely is degenerate
        "temp_range": (45.0, 60.0),
        "moisture_range": (55.0, 60.0),
        "o2_range": (5.0, 8.0),
        "co2_range": (3.0, 6.0),
        "nh3_relative": (0.25, 0.7),
    },
    {
        "id": 2,
        "name": "Peak Decomposition",
        # was (3,5,7) at a 55.0 floor: a worst-case sample cleared the EPA
        # Class A 55C-for-3-consecutive-days target by exactly zero margin.
        "duration_days": (4, 5, 7),
        "temp_range": (57.0, 65.0),
        "moisture_range": (50.0, 58.0),
        "o2_range": (2.0, 5.0),
        "co2_range": (6.0, 10.0),
        "nh3_relative": (0.7, 1.0),
    },
    {
        "id": 3,
        "name": "Cooling",
        "duration_days": (5, 8, 10),
        "temp_range": (30.0, 60.0),
        "moisture_range": (45.0, 55.0),
        "o2_range": (5.0, 12.0),
        "co2_range": (2.0, 6.0),
        "nh3_relative": (0.2, 0.6),
    },
    {
        "id": 4,
        "name": "Maturation",
        "duration_days": (24, 30, 36),
        "temp_range": (28.0, 32.0),  # was (22.0, 30.0) - converges to ambient
        "moisture_range": (40.0, 50.0),
        "o2_range": (12.0, 20.0),
        "co2_range": (0.5, 2.0),
        "nh3_relative": (0.0, 0.15),
    },
]