window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.5975,
    "madj_delta_pp": 7.8,
    "save_rate": 0.75,
    "save_delta_pp": 12.5,
    "eval_pass_rate": 0.6667,
    "guardrail_catch_rate": 1.0,
    "compliance_coverage": 1.0
  },
  "trend": {
    "labels": [
      "Before",
      "After"
    ],
    "save": [
      0.625,
      0.75
    ],
    "madj": [
      0.52,
      0.5975
    ]
  },
  "drivers": [
    {
      "label": "Price-driven cancellation",
      "share": 28,
      "save_rate": 0.8
    },
    {
      "label": "Low usage",
      "share": 28,
      "save_rate": 0.6
    },
    {
      "label": "Switching to competitor",
      "share": 28,
      "save_rate": 0.4
    },
    {
      "label": "Price-driven churn",
      "share": 11,
      "save_rate": 1.0
    },
    {
      "label": "Price/Value Concern",
      "share": 6,
      "save_rate": 0.0
    }
  ],
  "offers": [
    {
      "label": "discount",
      "save_rate": 0.8,
      "margin_cost": 29.04,
      "rel_cost": 0.78
    },
    {
      "label": "pause",
      "save_rate": 0.778,
      "margin_cost": 37.19,
      "rel_cost": 1.0
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
      "generated_at": "2026-07-21T07:02:21.008442+00:00",
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
        "playbook_sha": "da14d8f26a35",
        "conversations": 18,
        "segment_save_rate": 0.625,
        "overall_save_rate": 0.5556
      },
      "after": {
        "policy": "discounts_enabled",
        "playbook_sha": "2f9467cdb2b2",
        "conversations": 18,
        "segment_save_rate": 0.75,
        "overall_save_rate": 0.6111,
        "eval_pass_rate": 0.667,
        "eval_coverage": 1.0
      },
      "lift": {
        "segment_save_pp": 12.5,
        "segment_madj_pp": 7.8,
        "overall_save_pp": 5.5
      },
      "guardrail_catch_rate": 1.0,
      "note": "Paired before/after on identical seeded customers. TWO variables changed together (discount policy + agent playbook), so this is a synthetic PAIRED demonstration of the flywheel on the treated (price-sensitive) segment \u2014 not an isolated causal estimate. Numbers vary run to run."
    }
  }
};
