window.KEEL_DATA = {
  "kpis": {
    "madj_save_rate": 0.4433,
    "madj_delta_pp": 18.5,
    "save_rate": 0.5,
    "save_delta_pp": 22.2,
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
      0.2778,
      0.5
    ],
    "madj": [
      0.2583,
      0.4433
    ]
  },
  "drivers": [
    {
      "label": "Price sensitivity",
      "share": 39,
      "save_rate": 0.571
    },
    {
      "label": "Low usage",
      "share": 28,
      "save_rate": 0.8
    },
    {
      "label": "Switching to competitor",
      "share": 17,
      "save_rate": 0.333
    },
    {
      "label": "Switching to competitor",
      "share": 11,
      "save_rate": 0.0
    },
    {
      "label": "Price sensitivity",
      "share": 6,
      "save_rate": 0.0
    }
  ],
  "offers": [
    {
      "label": "pause",
      "save_rate": 0.857,
      "margin_cost": 16.83,
      "rel_cost": 1.0
    },
    {
      "label": "discount",
      "save_rate": 0.5,
      "margin_cost": 26.13,
      "rel_cost": 1.55
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
