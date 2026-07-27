window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.1988,
    "madj_delta_pp": 12.0,
    "save_rate": 0.25,
    "save_delta_pp": 15.0,
    "eval_pass_rate": 0.6937,
    "eval_coverage": 0.9875,
    "guardrail_catch_rate": 1.0,
    "compliance_coverage": 1.0
  },
  "trend": {
    "labels": [
      "Before",
      "After"
    ],
    "save": [
      0.1,
      0.25
    ],
    "madj": [
      0.079,
      0.1988
    ]
  },
  "drivers": [
    {
      "label": "Price sensitivity",
      "share": 34,
      "save_rate": 0.111
    },
    {
      "label": "Price sensitivity",
      "share": 34,
      "save_rate": 0.278
    },
    {
      "label": "Switching to competitor",
      "share": 14,
      "save_rate": 0.136
    },
    {
      "label": "No longer using",
      "share": 11,
      "save_rate": 0.222
    },
    {
      "label": "Price sensitivity",
      "share": 8,
      "save_rate": 0.0
    }
  ],
  "offers": [
    {
      "label": "discount",
      "save_rate": 0.4,
      "margin_cost": 9.12,
      "rel_cost": 1.35
    },
    {
      "label": "pause",
      "save_rate": 0.167,
      "margin_cost": 6.75,
      "rel_cost": 1.0
    }
  ],
  "safety": {
    "catch_rate": 1.0,
    "compliance": 1.0,
    "jailbreaks": 6,
    "off_scope": 4,
    "pii": 4,
    "over_limit": 0,
    "policy_disabled": 55,
    "invalid_args": 0
  },
  "meta": {
    "conversations": 160,
    "treated_cohort_n": 60,
    "provenance": {
      "generated_at": "2026-07-25T07:30:18.467857+00:00",
      "run_id": "run-20260725T071548",
      "cohort_size": 80,
      "cohort_scenario_ids": [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        27,
        31,
        32,
        35,
        38,
        42,
        52,
        53,
        58,
        61,
        68,
        70,
        80,
        92,
        94,
        95,
        100,
        105,
        111,
        118,
        122,
        126,
        127,
        130,
        135,
        139,
        144,
        147,
        151,
        164,
        179,
        183,
        187,
        207,
        210,
        230,
        231,
        232,
        236,
        237,
        251,
        259,
        261,
        264,
        265,
        274,
        281,
        284,
        298,
        311,
        313,
        320,
        321,
        323,
        327,
        332,
        335
      ],
      "paired_cohort": true,
      "treated_cohort_n": 60,
      "treated_segment": "Price too high",
      "segment_selection": "loss-ranked over the seeded churn_reason label, gated on current-spec eval-eligible baseline conversations; consumed by Act via the persisted signal id (clustering does NOT drive it)",
      "intervention_signal_id": 6,
      "intervention_signal": {
        "segment": "Price too high",
        "recommended_lever": "discount",
        "recommended_action": "enable the discount lever for the 'Price too high' segment",
        "lever_compatible": true,
        "confidence": 1.0,
        "evidence": {
          "n": 30,
          "save_rate": 0.2,
          "loss": 24.0,
          "segment_ranking": [
            {
              "reason": "Price too high",
              "n": 30,
              "save_rate": 0.2,
              "loss": 24.0
            },
            {
              "reason": "No longer needed",
              "n": 8,
              "save_rate": 0.25,
              "loss": 6.0
            },
            {
              "reason": "Switched to competitor",
              "n": 6,
              "save_rate": 0.0,
              "loss": 6.0
            }
          ]
        },
        "offer_effectiveness": [
          {
            "offer": "pause",
            "n": 44,
            "save_rate": 0.205,
            "avg_margin_cost": 6.73
          },
          {
            "offer": "none",
            "n": 3,
            "save_rate": 0.0,
            "avg_margin_cost": 0.0
          }
        ],
        "unaddressable_higher_loss": [],
        "eval_eligibility": {
          "rule": "current-spec eval verdict = pass",
          "spec_version": "spec-c5868d7d7b08",
          "total": 80,
          "graded": 80,
          "eligible": 47,
          "excluded": 33,
          "coverage": 1.0
        }
      },
      "guardrail_version": "g-941a95159f9b",
      "variables_changed": [
        "discount_policy",
        "agent_playbook"
      ],
      "held_constant": [
        "cohort_scenarios",
        "starting_subscription_state",
        "eligibility"
      ],
      "identical_starting_state": true,
      "starting_state_sha": "world-cc0b6f6f6e67",
      "models": {
        "flagship": "gpt-5",
        "mini": "gpt-5-mini",
        "embedding": "text-embedding-3-small"
      },
      "baseline": {
        "policy": "discounts_disabled",
        "playbook_sha": "add93744da2a",
        "conversations": 80,
        "starting_state_sha": "world-cc0b6f6f6e67",
        "segment_save_rate": 0.1,
        "overall_save_rate": 0.1125
      },
      "after": {
        "policy": "discounts_enabled",
        "playbook_sha": "a68e48936007",
        "conversations": 80,
        "starting_state_sha": "world-cc0b6f6f6e67",
        "segment_save_rate": 0.25,
        "overall_save_rate": 0.2375,
        "eval_pass_rate": 0.8,
        "eval_coverage": 0.975
      },
      "lift": {
        "segment_save_pp": 15.0,
        "segment_madj_pp": 12.0,
        "overall_save_pp": 12.5
      },
      "guardrail_catch_rate": 1.0,
      "outcome_parity_gap": 0.023,
      "outcome_parity_observational": true,
      "note": "Paired before/after on identical seeded customers run from a byte-identical starting subscription state (same starting_state_sha in both arms \u2192 eligibility held constant). TWO variables changed together (discount policy + agent playbook), so this is a synthetic PAIRED demonstration of the flywheel on the treated (price-sensitive) segment \u2014 not an isolated causal estimate. Numbers vary run to run."
    }
  }
};
