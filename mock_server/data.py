"""Sample data for mock API server."""

# Sample projects
PROJECTS = {
    1: {
        "id": 1,
        "title": "Office Building Acoustic Assessment",
        "client_name": "Acme Construction",
        "status": "in_progress",
        "budget": "45000.00",
        "description": "Comprehensive acoustic assessment for new office building including ASTC and AIIC testing.",
        "start_date": "2026-01-15",
        "end_date": "2026-03-30",
    },
    2: {
        "id": 2,
        "title": "Residential HVAC Noise Study",
        "client_name": "Green Homes LLC",
        "status": "in_progress",
        "budget": "12000.00",
        "description": "Evaluation of HVAC noise levels and mitigation recommendations for residential development.",
        "start_date": "2026-02-01",
        "end_date": "2026-02-28",
    },
    3: {
        "id": 3,
        "title": "Concert Hall Design Review",
        "client_name": "City Arts Foundation",
        "status": "pending",
        "budget": "75000.00",
        "description": "Acoustic design review and recommendations for new concert hall construction.",
        "start_date": "2026-04-01",
        "end_date": "2026-08-30",
    },
}

# Sample project contexts (rich data for agents)
PROJECT_CONTEXTS = {
    1: {
        "project": PROJECTS[1],
        "scope": [
            "ASTC testing per ASTM E336-17a for 12 party walls",
            "AIIC testing per ASTM E1007-16 for 8 floor assemblies",
            "Background noise measurements per ANSI S12.2",
            "Acoustic modeling and recommendations report",
        ],
        "standards": [
            {"type": "ASTM", "id": "ASTM E336-17a"},
            {"type": "ASTM", "id": "ASTM E1007-16"},
            {"type": "ANSI", "id": "ANSI S12.2"},
        ],
        "recent_files": [
            {
                "id": 101,
                "title": "Site Survey Report",
                "file_type": "pdf",
                "url": "/files/101/site-survey.pdf",
                "created_at": "2026-01-20",
            },
            {
                "id": 102,
                "title": "Floor Plans",
                "file_type": "pdf",
                "url": "/files/102/floor-plans.pdf",
                "created_at": "2026-01-18",
            },
        ],
        "proposals": [
            {
                "id": 1,
                "title": "Acoustic Assessment Proposal",
                "status": "accepted",
                "amount": "45000.00",
            }
        ],
        "metadata": {
            "client_technical_level": "moderate",
            "priority": "high",
            "notes": "Client prefers detailed technical reports with visual aids.",
        },
    },
    2: {
        "project": PROJECTS[2],
        "scope": [
            "HVAC equipment noise measurements",
            "Ductwork transmission loss evaluation",
            "NC/RC rating assessment",
            "Mitigation recommendations",
        ],
        "standards": [
            {"type": "ASHRAE", "id": "ASHRAE Handbook - HVAC Applications Ch. 48"},
            {"type": "ANSI", "id": "ANSI S12.2"},
        ],
        "recent_files": [
            {
                "id": 201,
                "title": "HVAC Specifications",
                "file_type": "pdf",
                "url": "/files/201/hvac-specs.pdf",
                "created_at": "2026-02-05",
            },
        ],
        "proposals": [
            {
                "id": 2,
                "title": "HVAC Noise Study Proposal",
                "status": "accepted",
                "amount": "12000.00",
            }
        ],
        "metadata": {
            "client_technical_level": "low",
            "priority": "medium",
            "notes": "Client is non-technical, needs accessible explanations.",
        },
    },
    3: {
        "project": PROJECTS[3],
        "scope": [
            "Room acoustic analysis and RT60 predictions",
            "Stage house acoustic design review",
            "Audience chamber optimization",
            "HVAC noise control review",
            "Sound isolation recommendations",
        ],
        "standards": [
            {"type": "ISO", "id": "ISO 3382-1"},
            {"type": "ANSI", "id": "ANSI S12.60"},
        ],
        "recent_files": [],
        "proposals": [
            {
                "id": 3,
                "title": "Concert Hall Design Review Proposal",
                "status": "pending",
                "amount": "75000.00",
            }
        ],
        "metadata": {
            "client_technical_level": "high",
            "priority": "low",
            "notes": "Client has in-house acoustician, expects peer-review level detail.",
        },
    },
}

# Sample clients
CLIENTS = {
    1: {
        "id": 1,
        "name": "Acme Construction",
        "contact_name": "John Smith",
        "email": "jsmith@acme-construction.com",
        "phone": "555-0100",
        "address": "123 Builder Lane, Construction City, CC 12345",
    },
    2: {
        "id": 2,
        "name": "Green Homes LLC",
        "contact_name": "Sarah Green",
        "email": "sarah@greenhomes.com",
        "phone": "555-0200",
        "address": "456 Eco Street, Greenville, GV 67890",
    },
    3: {
        "id": 3,
        "name": "City Arts Foundation",
        "contact_name": "Michael Arts",
        "email": "michael@cityarts.org",
        "phone": "555-0300",
        "address": "789 Culture Ave, Arts District, AD 11111",
    },
}

# Valid API token for testing
VALID_TOKEN = "test-token-12345"
