window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.5983,
    "madj_delta_pp": 13.3,
    "save_rate": 0.6667,
    "save_delta_pp": 16.7,
    "eval_pass_rate": 0.9444,
    "guardrail_catch_rate": 1.0,
    "compliance_coverage": 1.0
  },
  "trend": {
    "labels": [
      "Before",
      "After"
    ],
    "save": [
      0.5,
      0.6667
    ],
    "madj": [
      0.465,
      0.5983
    ]
  },
  "drivers": [
    {
      "label": "Switching to competitor",
      "share": 28,
      "save_rate": 0.2
    },
    {
      "label": "Price/Value Concerns",
      "share": 22,
      "save_rate": 1.0
    },
    {
      "label": "Pricing/Cost Concerns",
      "share": 22,
      "save_rate": 0.75
    },
    {
      "label": "No longer using",
      "share": 17,
      "save_rate": 1.0
    },
    {
      "label": "Low Usage",
      "share": 11,
      "save_rate": 0.5
    }
  ],
  "offers": [
    {
      "label": "discount",
      "save_rate": 1.0,
      "margin_cost": 15.13,
      "rel_cost": 1.05
    },
    {
      "label": "pause",
      "save_rate": 0.818,
      "margin_cost": 14.44,
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
    "conversations": 18
  }
};
