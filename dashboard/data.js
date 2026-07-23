window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.5945,
    "madj_delta_pp": 15.7,
    "save_rate": 0.75,
    "save_delta_pp": 20.0,
    "eval_pass_rate": 0.8167,
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
      0.55,
      0.75
    ],
    "madj": [
      0.438,
      0.5945
    ]
  },
  "drivers": [
    {
      "label": "Price/Value Concerns",
      "share": 28,
      "save_rate": 0.824
    },
    {
      "label": "Price sensitivity",
      "share": 27,
      "save_rate": 0.75
    },
    {
      "label": "Not using service",
      "share": 17,
      "save_rate": 0.2
    },
    {
      "label": "Switching to competitor",
      "share": 17,
      "save_rate": 0.0
    },
    {
      "label": "Price sensitivity",
      "share": 12,
      "save_rate": 0.0
    }
  ],
  "offers": [
    {
      "label": "discount",
      "save_rate": 0.667,
      "margin_cost": 26.53,
      "rel_cost": 1.59
    },
    {
      "label": "pause",
      "save_rate": 0.585,
      "margin_cost": 16.73,
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
      "generated_at": "2026-07-23T04:42:40.406376+00:00",
      "run_id": "run-20260723T043723",
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
      "intervention_signal_id": 8,
      "intervention_signal": {
        "confidence": 1.0,
        "evidence": {
          "loss": 9.0,
          "n": 20,
          "save_rate": 0.55,
          "segment_ranking": [
            {
              "loss": 9.0,
              "n": 20,
              "reason": "Price too high",
              "save_rate": 0.55
            },
            {
              "loss": 5.0,
              "n": 5,
              "reason": "Switched to competitor",
              "save_rate": 0.0
            },
            {
              "loss": 4.0,
              "n": 5,
              "reason": "No longer needed",
              "save_rate": 0.2
            }
          ]
        },
        "lever_compatible": true,
        "offer_effectiveness": [
          {
            "avg_margin_cost": 16.98,
            "n": 21,
            "offer": "pause",
            "save_rate": 0.571
          },
          {
            "avg_margin_cost": 0.0,
            "n": 9,
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
        "playbook_sha": "fce78dc90e34",
        "conversations": 30,
        "starting_state_sha": "world-813a7021a362",
        "segment_save_rate": 0.55,
        "overall_save_rate": 0.4
      },
      "after": {
        "policy": "discounts_enabled",
        "playbook_sha": "dd87d9e7f948",
        "conversations": 30,
        "starting_state_sha": "world-813a7021a362",
        "segment_save_rate": 0.75,
        "overall_save_rate": 0.5333,
        "eval_pass_rate": 0.817,
        "eval_coverage": 1.0
      },
      "lift": {
        "segment_save_pp": 20.0,
        "segment_madj_pp": 15.7,
        "overall_save_pp": 13.3
      },
      "guardrail_catch_rate": 1.0,
      "note": "Paired before/after on identical seeded customers run from a byte-identical starting subscription state (same starting_state_sha in both arms \u2192 eligibility held constant). TWO variables changed together (discount policy + agent playbook), so this is a synthetic PAIRED demonstration of the flywheel on the treated (price-sensitive) segment \u2014 not an isolated causal estimate. Numbers vary run to run."
    }
  }
};
