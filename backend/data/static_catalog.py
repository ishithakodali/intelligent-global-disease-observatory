CATALOG: dict[str, dict] = {
    "Tuberculosis|India": {
        "classification": {
            "disease": "Tuberculosis",
            "icd": "ICD-10: A15-A19",
            "type": "Bacterial infection",
            "subtype": "Pulmonary and extrapulmonary",
        },
        "epi": {
            "region": "India",
            "metric_label": "Cases",
            "cases_by_year": [
                {"year": 2018, "cases": 2500000},
                {"year": 2019, "cases": 2700000},
                {"year": 2020, "cases": 2400000},
                {"year": 2021, "cases": 2600000},
                {"year": 2022, "cases": 2780000},
                {"year": 2023, "cases": 2910000},
            ],
            "outbreak_alerts": [
                {
                    "date": "2024-06-14",
                    "source": "WHO",
                    "alert": "Multidrug-resistant TB cluster increase in two urban districts.",
                    "severity": "high",
                },
                {
                    "date": "2024-11-02",
                    "source": "ProMED",
                    "alert": "Correctional-facility TB transmission alert issued.",
                    "severity": "moderate",
                },
                {
                    "date": "2025-01-19",
                    "source": "HealthMap",
                    "alert": "Regional spike associated with delayed diagnosis events.",
                    "severity": "moderate",
                },
            ],
            "country_comparison": [
                {"country": "India", "cases": 2910000},
                {"country": "Indonesia", "cases": 1090000},
                {"country": "China", "cases": 790000},
                {"country": "Pakistan", "cases": 620000},
                {"country": "Nigeria", "cases": 500000},
            ],
        },
        "genes": [
            {
                "symbol": "TLR2",
                "score": 0.86,
                "summary": "Innate immunity signaling linked to TB susceptibility.",
            },
            {
                "symbol": "SLC11A1",
                "score": 0.81,
                "summary": "Macrophage iron transport and host-pathogen response.",
            },
            {
                "symbol": "VDR",
                "score": 0.74,
                "summary": "Vitamin D receptor variants associated with progression risk.",
            },
        ],
        "therapy": {
            "drug": "Isoniazid",
            "mechanism": "Inhibits mycolic acid synthesis in mycobacterial cell walls.",
            "who_essential": "Yes",
            "guideline": "First-line regimen with rifampicin, pyrazinamide, and ethambutol during intensive phase.",
        },
    },
    "Dengue|India": {
        "classification": {
            "disease": "Dengue",
            "icd": "ICD-10: A90-A91",
            "type": "Viral infection",
            "subtype": "Arboviral, mosquito-borne",
        },
        "epi": {
            "region": "India",
            "metric_label": "Cases",
            "cases_by_year": [
                {"year": 2018, "cases": 101000},
                {"year": 2019, "cases": 136000},
                {"year": 2020, "cases": 44000},
                {"year": 2021, "cases": 193000},
                {"year": 2022, "cases": 154000},
                {"year": 2023, "cases": 201000},
            ],
            "outbreak_alerts": [
                {
                    "date": "2024-08-22",
                    "source": "HealthMap",
                    "alert": "Monsoon-linked dengue surge in western states.",
                    "severity": "high",
                },
                {
                    "date": "2025-02-06",
                    "source": "WHO",
                    "alert": "Serotype shift detected in urban clusters.",
                    "severity": "moderate",
                },
            ],
            "country_comparison": [
                {"country": "India", "cases": 201000},
                {"country": "Brazil", "cases": 1600000},
                {"country": "Thailand", "cases": 150000},
                {"country": "Vietnam", "cases": 190000},
                {"country": "Philippines", "cases": 120000},
            ],
        },
        "genes": [
            {
                "symbol": "IFITM3",
                "score": 0.72,
                "summary": "Interferon-induced antiviral restriction associations reported.",
            },
            {
                "symbol": "HLA-DQB1",
                "score": 0.67,
                "summary": "Host immunogenetic severity association.",
            },
        ],
        "therapy": {
            "drug": "Supportive therapy",
            "mechanism": "Fluid management, platelet and hematocrit monitoring.",
            "who_essential": "Yes (supportive agents)",
            "guideline": "No direct antiviral standard of care; early warning triage reduces mortality.",
        },
    },
}
