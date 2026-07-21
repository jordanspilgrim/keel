window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.5988,
    "madj_delta_pp": 30.2,
    "save_rate": 0.75,
    "save_delta_pp": 37.5,
    "eval_pass_rate": 0.9444,
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
      0.375,
      0.75
    ],
    "madj": [
      0.2963,
      0.5988
    ]
  },
  "drivers": [
    {
      "label": "Price sensitivity",
      "share": 39,
      "save_rate": 0.857
    },
    {
      "label": "No longer using",
      "share": 28,
      "save_rate": 0.8
    },
    {
      "label": "Switching to competitor",
      "share": 17,
      "save_rate": 0.667
    },
    {
      "label": "Switched to competitor",
      "share": 11,
      "save_rate": 0.0
    },
    {
      "label": "Price/Value Concern",
      "share": 6,
      "save_rate": 0.0
    }
  ],
  "offers": [
    {
      "label": "pause",
      "save_rate": 0.875,
      "margin_cost": 30.98,
      "rel_cost": 1.0
    },
    {
      "label": "discount",
      "save_rate": 0.833,
      "margin_cost": 38.5,
      "rel_cost": 1.24
    }
  ],
  "safety": {
    "catch_rate": 1.0,
    "compliance": 1.0,
    "jailbreaks": 4,
    "off_scope": 4,
    "pii": 3,
    "over_limit": 0
  },
  "meta": {
    "conversations": 18,
    "provenance": {
      "generated_at": "2026-07-21T08:13:24.740251+00:00",
      "cohort_size": 18,
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
        38
      ],
      "paired_cohort": true,
      "treated_segment": "Price too high",
      "segment_selection": "data-driven (highest loss impact in baseline)",
      "baseline_segments": [
        {
          "reason": "Price too high",
          "n": 8,
          "save_rate": 0.375,
          "loss": 5.0
        },
        {
          "reason": "Switched to competitor",
          "n": 5,
          "save_rate": 0.2,
          "loss": 4.0
        },
        {
          "reason": "No longer needed",
          "n": 5,
          "save_rate": 0.8,
          "loss": 1.0
        }
      ],
      "variables_changed": [
        "discount_policy",
        "agent_playbook"
      ],
      "models": {
        "flagship": "gpt-5",
        "mini": "gpt-5-mini",
        "embedding": "text-embedding-3-small"
      },
      "baseline": {
        "policy": "discounts_disabled",
        "playbook_sha": "8814fd1fd6ec",
        "conversations": 18,
        "segment_save_rate": 0.375,
        "overall_save_rate": 0.4444
      },
      "after": {
        "policy": "discounts_enabled",
        "playbook_sha": "4d3125774545",
        "conversations": 18,
        "segment_save_rate": 0.75,
        "overall_save_rate": 0.6667,
        "eval_pass_rate": 0.944,
        "eval_coverage": 1.0
      },
      "lift": {
        "segment_save_pp": 37.5,
        "segment_madj_pp": 30.2,
        "overall_save_pp": 22.2
      },
      "guardrail_catch_rate": 1.0,
      "note": "Paired before/after on identical seeded customers. TWO variables changed together (discount policy + agent playbook), so this is a synthetic PAIRED demonstration of the flywheel on the treated (price-sensitive) segment \u2014 not an isolated causal estimate. Numbers vary run to run."
    }
  }
};
