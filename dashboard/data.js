window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.4115,
    "madj_delta_pp": 29.3,
    "save_rate": 0.5,
    "save_delta_pp": 35.0,
    "eval_pass_rate": 1.0,
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
      0.15,
      0.5
    ],
    "madj": [
      0.1185,
      0.4115
    ]
  },
  "drivers": [
    {
      "label": "Price sensitivity",
      "share": 37,
      "save_rate": 0.545
    },
    {
      "label": "Price sensitivity",
      "share": 27,
      "save_rate": 0.5
    },
    {
      "label": "Low usage",
      "share": 17,
      "save_rate": 0.4
    },
    {
      "label": "Switching to competitor",
      "share": 17,
      "save_rate": 0.2
    },
    {
      "label": "Price sensitivity",
      "share": 3,
      "save_rate": 0.0
    }
  ],
  "offers": [
    {
      "label": "discount",
      "save_rate": 0.6,
      "margin_cost": 33.08,
      "rel_cost": 7.69
    },
    {
      "label": "pause",
      "save_rate": 0.438,
      "margin_cost": 4.3,
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
    "conversations": 30,
    "provenance": {
      "generated_at": "2026-07-22T02:05:28.592381+00:00",
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
      "intervention_signal_id": 1,
      "intervention_signal": {
        "confidence": 1.0,
        "evidence": {
          "loss": 17.0,
          "n": 20,
          "save_rate": 0.15,
          "segment_ranking": [
            {
              "loss": 17.0,
              "n": 20,
              "reason": "Price too high",
              "save_rate": 0.15
            },
            {
              "loss": 3.0,
              "n": 5,
              "reason": "Switched to competitor",
              "save_rate": 0.4
            },
            {
              "loss": 2.0,
              "n": 5,
              "reason": "No longer needed",
              "save_rate": 0.6
            }
          ]
        },
        "lever_compatible": true,
        "offer_effectiveness": [
          {
            "avg_margin_cost": 10.28,
            "n": 17,
            "offer": "pause",
            "save_rate": 0.471
          },
          {
            "avg_margin_cost": 0.0,
            "n": 13,
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
      "models": {
        "flagship": "gpt-5",
        "mini": "gpt-5-mini",
        "embedding": "text-embedding-3-small"
      },
      "baseline": {
        "policy": "discounts_disabled",
        "playbook_sha": "fce78dc90e34",
        "conversations": 30,
        "segment_save_rate": 0.15,
        "overall_save_rate": 0.2667
      },
      "after": {
        "policy": "discounts_enabled",
        "playbook_sha": "dd87d9e7f948",
        "conversations": 30,
        "segment_save_rate": 0.5,
        "overall_save_rate": 0.4333,
        "eval_pass_rate": 1.0,
        "eval_coverage": 1.0
      },
      "lift": {
        "segment_save_pp": 35.0,
        "segment_madj_pp": 29.3,
        "overall_save_pp": 16.7
      },
      "guardrail_catch_rate": 1.0,
      "note": "Paired before/after on identical seeded customers. TWO variables changed together (discount policy + agent playbook), so this is a synthetic PAIRED demonstration of the flywheel on the treated (price-sensitive) segment \u2014 not an isolated causal estimate. Numbers vary run to run."
    }
  }
};
