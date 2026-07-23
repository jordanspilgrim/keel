window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.566,
    "madj_delta_pp": 28.9,
    "save_rate": 0.7,
    "save_delta_pp": 35.0,
    "eval_pass_rate": 0.9167,
    "eval_coverage": 1.0,
    "guardrail_catch_rate": 1.0,
    "compliance_coverage": 1.0
  },
  "trend": {
    "labels": [
      "Before",
      "After"
    ],
    "save": [
      0.35,
      0.7
    ],
    "madj": [
      0.2765,
      0.566
    ]
  },
  "drivers": [
    {
      "label": "Price sensitivity",
      "share": 35,
      "save_rate": 0.714
    },
    {
      "label": "Price sensitivity",
      "share": 18,
      "save_rate": 0.545
    },
    {
      "label": "Not using service",
      "share": 17,
      "save_rate": 0.5
    },
    {
      "label": "Switching to competitor",
      "share": 17,
      "save_rate": 0.2
    },
    {
      "label": "Price Concerns",
      "share": 13,
      "save_rate": 0.0
    }
  ],
  "offers": [
    {
      "label": "discount",
      "save_rate": 0.833,
      "margin_cost": 25.17,
      "rel_cost": 1.64
    },
    {
      "label": "pause",
      "save_rate": 0.59,
      "margin_cost": 15.34,
      "rel_cost": 1.0
    }
  ],
  "safety": {
    "catch_rate": 1.0,
    "compliance": 1.0,
    "jailbreaks": 6,
    "off_scope": 4,
    "pii": 4,
    "over_limit": 19
  },
  "meta": {
    "conversations": 60,
    "provenance": {
      "generated_at": "2026-07-23T17:18:36.935722+00:00",
      "run_id": "run-20260723T171058",
      "cohort_size": 30,
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
        100
      ],
      "paired_cohort": true,
      "treated_segment": "Price too high",
      "segment_selection": "data-driven (structured intervention signal consumed by Act)",
      "intervention_signal_id": 6,
      "intervention_signal": {
        "confidence": 1.0,
        "evidence": {
          "loss": 13.0,
          "n": 20,
          "save_rate": 0.35,
          "segment_ranking": [
            {
              "loss": 13.0,
              "n": 20,
              "reason": "Price too high",
              "save_rate": 0.35
            },
            {
              "loss": 5.0,
              "n": 5,
              "reason": "Switched to competitor",
              "save_rate": 0.0
            },
            {
              "loss": 3.0,
              "n": 5,
              "reason": "No longer needed",
              "save_rate": 0.4
            }
          ]
        },
        "lever_compatible": true,
        "offer_effectiveness": [
          {
            "avg_margin_cost": 16.37,
            "n": 19,
            "offer": "pause",
            "save_rate": 0.474
          },
          {
            "avg_margin_cost": 0.0,
            "n": 11,
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
      "starting_state_sha": "world-813a7021a362",
      "models": {
        "flagship": "gpt-5",
        "mini": "gpt-5-mini",
        "embedding": "text-embedding-3-small"
      },
      "baseline": {
        "policy": "discounts_disabled",
        "playbook_sha": "add93744da2a",
        "conversations": 30,
        "starting_state_sha": "world-813a7021a362",
        "segment_save_rate": 0.35,
        "overall_save_rate": 0.3
      },
      "after": {
        "policy": "discounts_enabled",
        "playbook_sha": "a68e48936007",
        "conversations": 30,
        "starting_state_sha": "world-813a7021a362",
        "segment_save_rate": 0.7,
        "overall_save_rate": 0.6333,
        "eval_pass_rate": 0.917,
        "eval_coverage": 1.0
      },
      "lift": {
        "segment_save_pp": 35.0,
        "segment_madj_pp": 28.9,
        "overall_save_pp": 33.3
      },
      "guardrail_catch_rate": 1.0,
      "note": "Paired before/after on identical seeded customers run from a byte-identical starting subscription state (same starting_state_sha in both arms \u2192 eligibility held constant). TWO variables changed together (discount policy + agent playbook), so this is a synthetic PAIRED demonstration of the flywheel on the treated (price-sensitive) segment \u2014 not an isolated causal estimate. Numbers vary run to run."
    }
  }
};
