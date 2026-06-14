import random

def fetch_ha_state(entity_id):
    """
    MOCK API: Simulates responses from Home Assistant.
    """
    if entity_id == "cover.garage_door":
        state = random.choice(["open", "closed"])
        return {"state": state, "attributes": {"friendly_name": "Main Garage"}}

    elif entity_id == "sensor.solaredge_current_power":
        return {"state": str(round(random.uniform(2100.0, 7500.0), 2))}

    elif entity_id == "sensor.solaredge_meter_power":
        return {"state": str(round(random.uniform(-3000.0, 1500.0), 2))}
        
    elif entity_id == "sensor.solaredge_inverter_1":
        return {"state": str(round(random.uniform(1500.0, 3000.0), 2))}
        
    elif entity_id == "sensor.solaredge_inverter_2":
        return {"state": str(round(random.uniform(1500.0, 3000.0), 2))}

    elif entity_id == "sensor.solaredge_panel_array":
        panel_dict = {f"Panel {i}": round(random.uniform(50.0, 350.0), 1) for i in range(1, 68)}
        return {"attributes": {"panels": panel_dict}}

    return {"error": "Entity not found"}

def post_ha_service(domain, service, entity_id):
    print(f"MOCK COMMAND: Sent {service} command to {entity_id} via {domain}")
    return True