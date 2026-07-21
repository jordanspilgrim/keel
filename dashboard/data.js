window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.4,
    "madj_delta_pp": 10.4,
    "save_rate": 0.5,
    "save_delta_pp": 12.5,
    "eval_pass_rate": 0.8889,
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
      0.5
    ],
    "madj": [
      0.2963,
      0.4
    ]
  },
  "drivers": [
    {
      "label": "Price-sensitive churn",
      "share": 39,
      "save_rate": 0.571
    },
    {
      "label": "No longer using",
      "share": 28,
      "save_rate": 0.4
    },
    {
      "label": "Switching to competitor",
      "share": 22,
      "save_rate": 0.0
    },
    {
      "label": "Price Concerns",
      "share": 6,
      "save_rate": 0.0
    },
    {
      "label": "Switched for Price",
      "share": 6,
      "save_rate": 0.0
    }
  ],
  "offers": [
    {
      "label": "discount",
      "save_rate": 0.667,
      "margin_cost": 37.53,
      "rel_cost": 2.37
    },
    {
      "label": "pause",
      "save_rate": 0.286,
      "margin_cost": 15.84,
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
      "generated_at": "2026-07-21T22:45:11.559133+00:00",
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
      "segment_selection": "data-driven (structured intervention signal consumed by Act)",
      "intervention_signal_id": 6,
      "intervention_signal": {
        "segment": "Price too high",
        "recommended_lever": "discount",
        "recommended_action": "enable the discount lever for the 'Price too high' segment",
        "lever_compatible": true,
        "confidence": 1.0,
        "evidence": {
          "n": 8,
          "save_rate": 0.375,
          "loss": 5.0,
          "segment_ranking": [
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
              "save_rate": 0.6,
              "loss": 2.0
            }
          ]
        },
        "offer_effectiveness": [
          {
            "offer": "pause",
            "n": 12,
            "save_rate": 0.583,
            "avg_margin_cost": 13.0
          },
          {
            "offer": "none",
            "n": 6,
            "save_rate": 0.0,
            "avg_margin_cost": 0.0
          }
        ],
        "unaddressable_higher_loss": []
      },
      "guardrail_version": "3",
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
        "conversations": 18,
        "segment_save_rate": 0.375,
        "overall_save_rate": 0.3889
      },
      "after": {
        "policy": "discounts_enabled",
        "playbook_sha": "dd87d9e7f948",
        "conversations": 18,
        "segment_save_rate": 0.5,
        "overall_save_rate": 0.3333,
        "eval_pass_rate": 0.889,
        "eval_coverage": 1.0
      },
      "lift": {
        "segment_save_pp": 12.5,
        "segment_madj_pp": 10.4,
        "overall_save_pp": -5.6
      },
      "guardrail_catch_rate": 1.0,
      "note": "Paired before/after on identical seeded customers. TWO variables changed together (discount policy + agent playbook), so this is a synthetic PAIRED demonstration of the flywheel on the treated (price-sensitive) segment \u2014 not an isolated causal estimate. Numbers vary run to run."
    }
  }
};
