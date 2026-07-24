window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.2917,
    "madj_delta_pp": 14.7,
    "save_rate": 0.3667,
    "save_delta_pp": 18.3,
    "eval_pass_rate": 0.8313,
    "eval_coverage": 0.9938,
    "guardrail_catch_rate": 1.0,
    "compliance_coverage": 1.0
  },
  "trend": {
    "labels": [
      "Before",
      "After"
    ],
    "save": [
      0.1833,
      0.3667
    ],
    "madj": [
      0.1448,
      0.2917
    ]
  },
  "drivers": [
    {
      "label": "Pricing Concern",
      "share": 36,
      "save_rate": 0.483
    },
    {
      "label": "Price-driven churn",
      "share": 22,
      "save_rate": 0.167
    },
    {
      "label": "Price Concerns",
      "share": 18,
      "save_rate": 0.0
    },
    {
      "label": "Switching to competitor",
      "share": 12,
      "save_rate": 0.158
    },
    {
      "label": "No longer using",
      "share": 11,
      "save_rate": 0.167
    }
  ],
  "offers": [
    {
      "label": "pause",
      "save_rate": 0.351,
      "margin_cost": 11.19,
      "rel_cost": 1.0
    },
    {
      "label": "discount",
      "save_rate": 0.231,
      "margin_cost": 2.42,
      "rel_cost": 0.22
    }
  ],
  "safety": {
    "catch_rate": 1.0,
    "compliance": 1.0,
    "jailbreaks": 6,
    "off_scope": 4,
    "pii": 4,
    "over_limit": 54
  },
  "meta": {
    "conversations": 160,
    "provenance": {
      "generated_at": "2026-07-24T00:01:19.239200+00:00",
      "run_id": "run-20260723T234900",
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
      "segment_selection": "data-driven (structured intervention signal consumed by Act)",
      "intervention_signal_id": 6,
      "intervention_signal": {
        "confidence": 1.0,
        "evidence": {
          "loss": 49.0,
          "n": 60,
          "save_rate": 0.183,
          "segment_ranking": [
            {
              "loss": 49.0,
              "n": 60,
              "reason": "Price too high",
              "save_rate": 0.183
            },
            {
              "loss": 6.0,
              "n": 9,
              "reason": "No longer needed",
              "save_rate": 0.333
            },
            {
              "loss": 6.0,
              "n": 8,
              "reason": "Switched to competitor",
              "save_rate": 0.25
            }
          ]
        },
        "lever_compatible": true,
        "offer_effectiveness": [
          {
            "avg_margin_cost": 9.05,
            "n": 53,
            "offer": "pause",
            "save_rate": 0.302
          },
          {
            "avg_margin_cost": 0.0,
            "n": 27,
            "offer": "none",
            "save_rate": 0.0
          }
        ],
        "recommended_action": "enable the discount lever for the 'Price too high' segment",
        "recommended_lever": "discount",
        "segment": "Price too high",
        "unaddressable_higher_loss": []
      },
      "guardrail_version": "4",
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
        "segment_save_rate": 0.1833,
        "overall_save_rate": 0.2
      },
      "after": {
        "policy": "discounts_enabled",
        "playbook_sha": "a68e48936007",
        "conversations": 80,
        "starting_state_sha": "world-cc0b6f6f6e67",
        "segment_save_rate": 0.3667,
        "overall_save_rate": 0.3,
        "eval_pass_rate": 0.831,
        "eval_coverage": 0.994
      },
      "lift": {
        "segment_save_pp": 18.3,
        "segment_madj_pp": 14.7,
        "overall_save_pp": 10.0
      },
      "guardrail_catch_rate": 1.0,
      "fairness_gap": 0.045,
      "note": "Paired before/after on identical seeded customers run from a byte-identical starting subscription state (same starting_state_sha in both arms \u2192 eligibility held constant). TWO variables changed together (discount policy + agent playbook), so this is a synthetic PAIRED demonstration of the flywheel on the treated (price-sensitive) segment \u2014 not an isolated causal estimate. Numbers vary run to run."
    }
  }
};
