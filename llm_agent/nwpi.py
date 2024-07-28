import json
import requests
from dotenv import load_dotenv
import os
import re
from langchain.agents import tool
from typing import List, Optional
import time
from datetime import datetime, timedelta
load_dotenv()


vmanage_host = os.getenv("VMANAGE_IP")
vmanage_port = os.getenv("VMANAGE_PORT")
vmanage_username = os.getenv("VMANAGE_USER")
vmanage_password = os.getenv("VMANAGE_PASS")

class Authentication:

    @staticmethod
    def get_jsessionid(vmanage_host, vmanage_port, username, password):
        api = "/j_security_check"
        base_url = "https://%s:%s"%(vmanage_host, vmanage_port)
        url = base_url + api
        payload = {'j_username' : username, 'j_password' : password}

        response = requests.post(url=url, data=payload, verify=False)
        try:
            cookies = response.headers["Set-Cookie"]
            jsessionid = cookies.split(";")
            return(jsessionid[0])
        except:
            print("No valid JSESSION ID returned\n")
            exit()

    @staticmethod
    def get_token(vmanage_host, vmanage_port, jsessionid):
        headers = {'Cookie': jsessionid}
        base_url = "https://%s:%s"%(vmanage_host, vmanage_port)
        api = "/dataservice/client/token"
        url = base_url + api      
        response = requests.get(url=url, headers=headers, verify=False)
        if response.status_code == 200:
            return(response.text)
        else:
            return None

Auth = Authentication()
jsessionid = Auth.get_jsessionid(vmanage_host,vmanage_port,vmanage_username,vmanage_password)
token = Auth.get_token(vmanage_host,vmanage_port,jsessionid)

if token is not None:
    header = {'Content-Type': "application/json",'Cookie': jsessionid, 'X-XSRF-TOKEN': token}
else:
    header = {'Content-Type': "application/json",'Cookie': jsessionid}

@tool
def get_device_details_from_site(site: int) -> list:
    """
    Retrieve information from the devices available in the given site. This is required prior starting a trace. 

    Args:
        site (int): Must come from user and indicates from what site we shoule retrieve the device list. 

    Returns:
        device_list (list): A list of the available devices on the site along with system_ip, uuid and version.
    """
    return _get_device_details_from_site(site)

def _get_device_details_from_site(site: int) -> list:

    url = "https://%s/dataservice/health/devices?page_size=12000&site-id=%s"%(vmanage_host, site)

    response = requests.request("GET", url, headers=header, verify=False)
    
    device_list = []
    if response.status_code == 200:
        resp = response.json()

        device_list = []
        for device in resp["devices"]:
            if device["reachability"] == "reachable":
                system_ip = device["system_ip"]
                uuid = device["uuid"]
                version = device["software_version"]
        
            device_info = {
                "local-system-ip": system_ip,
                "deviceId" : system_ip,
                "uuid" : uuid,
                "version" : version
            }
            
            device_list.append(device_info)   
        return device_list 
    else:
        print("Error:", response.status_code)
        return []

@tool
def start_trace(device_list: list, site: str, vpn: str, src: Optional[str] = "", dst: Optional[str]="") -> tuple[str,int,str]:
    """
    Start a trace on the devices belonging to the site.
    Args:
        device_list (list): List of dictionaries. Each element represent one device and additional information required to start the trace. The list comes from "get_device_details_from_site" function. 
        site (int): Site where trace should be started. User must provide it. 
        vpn (int): VPN where the trace should be started. User must provide it. 
        src (str): Optional - Source subnet or host to filter the trace. User can provide it. 
        dst (str): Optional - Destionation subnet or host to filter the trace. User can provide it. 
    Returns:
        start_time (str): Epoch time when the trace was started.
        trace_id: (int): Identifier of trace that could be used to retrieve information.
        status: (str): Indicates if the trace is starting or had an error.
    """

    return _start_trace(device_list, site, vpn, src, dst)

def _start_trace(device_list: list, site: str, vpn: str, src: Optional[str] = "", dst: Optional[str]="") -> tuple[str,int,str]:

    url = "https://%s/dataservice/stream/device/nwpi/trace/start"%(vmanage_host)
    qos = "true"


    version_dict = {}
    version_list = []
    for device in device_list:
        version_string = device["version"]
        numeric_version = re.sub(r'\D', '', version_string)
        version = int(numeric_version[:4])
        version_dict[version] = version_string
        version_list.append(version)
    min_version = min(version_list)

    # If version is lower than 17.09 de-activate qos monitor
    if min_version < 1709:
        qos = "false"
    
    source_version  = version_dict[min_version]

    payload = json.dumps({
    "source-site": site,
    "device-list": device_list,
    "vpn-id": vpn,
    "src-pfx": src,
    "dst-pfx": dst,
    "art-vis": "true",
    "app-vis": "true",
    "qos-mon": qos,
    "duration": "20",
    "trace-name": "GENAI-Trace",
    "wan-drop-rate-threshold": 5,
    "local-drop-rate-threshold": 5,
    "source-site-version": source_version
    })

    response = requests.request("POST", url, headers=header, data=payload, verify=False)
    print(payload)
    if response.status_code == 200:
        resp = response.json()
        print(resp)
        start_time = resp["entry_time"]
        trace_id = resp["trace-id"]
        status = resp["action"]

        return start_time, trace_id, status
    else:
        print("Error:", response.status_code)
        return "","",""

@tool
def verify_trace_state(trace_id: int) -> tuple[str,str]:

    """
    Verify the trace is running correctly

    Args:
        trace_id (int): Trace ID to check status for. Must come from "start_trace"

    Returns:
        state (str): State of the trace. "running" or "stopped" is expected.
        message: (str): Additional information about the trace.     
    """

    return _verify_trace_state(trace_id)

def _verify_trace_state(trace_id: int) -> tuple[str,str]:

    url = "https://%s/dataservice/stream/device/nwpi/traceHistory"%(vmanage_host)

    response = requests.request("GET", url, headers=header, verify=False)

    if response.status_code == 200:
        resp = response.json()
        traces = resp["data"]
        state = ""
        message = ""
        for trace in traces:
            if trace["trace-id"] == trace_id:
                state = trace["data"]["summary"]["state"]
                message = trace["data"]["summary"]["message"]                
                return state, message
    
    return state, message

@tool
def trace_readout(trace_id: int, timestamp: int) -> dict:

    """
    Get important events about the trace.

    Args:
    trace_id (int): Trace id to search for
    timestamp (int): Epoch timestamp representing the start of the trace
    state (str): State of the trace, it is retrieved from the verify_trace_state function. 

    Returns:
    events_exist (bool): True indicates there are flows detected, false indicate there are no flows detected.
    all_events (list): Dictionary with the events detected. The key represents tha application and the value contains type of event and hops affected. An empty dictionary mean there are no events impacting traffic. 

    """
    return _trace_readout(trace_id, timestamp)

def _trace_readout(trace_id: int, timestamp: int) -> tuple[bool,dict]:

    url = "https://%s/dataservice/stream/device/nwpi/eventReadoutByTraces?trace_id=%s&entry_time=%s"%(vmanage_host, trace_id, timestamp)

    response = requests.request("GET", url, headers=header,verify=False)

    if response.status_code == 200:
        resp = response.json()
        data = resp.get("data",{})

        events = data[0]["detail"]
        events_exist = False
        all_events = {}
        if len(events) > 0:
            events_exist = True
            for app in events:
                if len(app["eventHopStatistics"]) > 0:
                    for event_hop_statistics in app["eventHopStatistics"]:
                        hop_with_edge = []
                        for hop_statistics in event_hop_statistics["hopStatistics"]:
                            hop_with_edge.append(hop_statistics["hopWithEdge"])
                        app_name = app["application"]
                        event = event_hop_statistics["event"]
                        all_events.update(
                            {app_name.upper()+"_"+event: hop_with_edge}
                        )
            return events_exist, all_events
    else:
        print("Error:", response.status_code)
    return events_exist, all_events


@tool
def get_site_list() -> list:

    """
    Get site lists.

    Returns:
    site_list (list): List with the sites ID. Empty list means traces cannot be ran. 

    """
    return _get_site_list()

def _get_site_list() -> list:

    url = "https://%s/dataservice/statistics/sitehealth/common?interval=30"%(vmanage_host)

    response = requests.request("GET", url, headers=header, verify=False)
    if response.status_code == 200:
        data = response.json()
        list = data.get("data",{})
        site_list = []
        if len(list) > 0:
            for site in list:
                site_list.append(site["site_id"])
            return site_list
    else:
         # Print an error message if the request was not successful
        print("Error:", response.status_code)
    return site_list

@tool
def get_entry_time_and_state(trace_id: int) -> tuple[int,str]:

    """
    Get trace entry_time

    Args:
        trace_id (int): Trace ID to retrieve the entry time from. 

    Returns:
       entry_time (int): Entry Time to use to retrieve readout information. Different from 0 is good. 
       state (str): State of the trace. "Running" or "Stopped" is expected. Useful to retrieve more information in other tools.
    """

    return _get_entry_time_and_state(trace_id)

def _get_entry_time_and_state(trace_id: int) -> tuple[int,str]:

    url = "https://%s/dataservice/stream/device/nwpi/traceHistory"%(vmanage_host)

    response = requests.request("GET", url, headers=header, verify=False)

    entry_time = 0
    state = ""
    if response.status_code == 200:
        resp = response.json()
        print(resp, "entry time and state")
        traces = resp["data"]
        for trace in traces:
            if trace["trace-id"] == trace_id:
                entry_time = trace["entry_time"]
                state = trace["data"]["summary"]["state"]
                return entry_time, state
    
    return entry_time, state

# @tool
# def get_aggregate_data(trace_id: int, timestamp: int, traceState: str) -> tuple[list,int,int]:

#     """
#     Get aggregate data of captured flows 

#     Args:
#         trace_id (int): Trace ID to retrieve the entry time from. 
#         timestamp (int): Epoch timestamp representing the start of the trace
#         traceState (str): State of the trace, it is retrieved from the verify_trace_status function. 

#     Returns:
#        drop_summary_list (list): List of dropped reason per device.
#        startime (int): epoch start time to filter further queries
#        endtime (int): epoch end time to filter further queries

#     """

#     return _get_aggregate_data(trace_id, timestamp, traceState)

# def _get_aggregate_data(trace_id: int, timestamp: int, traceState: str) -> tuple[list,int,int]:

#     url = "https://%s/dataservice/stream/device/nwpi/aggFlow?traceId=%s&timestamp=%s&traceState=%s"%(vmanage_host, trace_id, timestamp, traceState)

#     response = requests.request("GET", url, headers=header, verify=False)


#     if response.status_code == 200:
#         resp = response.json()
#         print(resp, "agg data")
#         traces = resp
#         drop_summary_list = []
#         counter = 0
#         start_time = 0
#         end_time = 0

#         for trace in traces:
#             counter += 1
#             if counter == 1:
#                 start_time = trace["data"]["start_timestamp"]
#             if counter == len(traces):
#                 end_time = trace["data"]["last_update_time"]
#             app_name = trace.get("data", {}).get("app_name", {})
#             drop_summary = {"upstream": [], "downstream": []}

#             for path in trace["data"]["path_list"]:
#                 upstream_devices = {}
#                 downstream_devices = {}
#                 for stream in path['upstream_hop_list']:
#                     local_drop = stream["local_drop_rate"]
#                     device_system_ip = stream["local_edge"] if local_drop != "0.00" else stream["remote_edge"]
#                     drops = [drop["display_name"] for drop in stream.get("local_drop_causes", []) + stream.get("remote_drop_causes", [])]

#                     if drops:
#                         if device_system_ip not in upstream_devices:
#                             upstream_devices[device_system_ip] = []
#                         upstream_devices[device_system_ip].extend(drops)
                    
#                 for stream in path['downstream_hop_list']:
#                     local_drop = stream["local_drop_rate"]
#                     device_system_ip = stream["local_edge"] if local_drop != "0.00" else stream["remote_edge"]
#                     drops = [drop["display_name"] for drop in stream.get("local_drop_causes", []) + stream.get("remote_drop_causes", [])]

#                     if drops:
#                         if device_system_ip not in downstream_devices:
#                             downstream_devices[device_system_ip] = []
#                         downstream_devices[device_system_ip].extend(drops)

#                 drop_summary["upstream"].extend([{device_ip: drops} for device_ip, drops in upstream_devices.items()])
#                 drop_summary["downstream"].extend([{device_ip: drops} for device_ip, drops in downstream_devices.items()])

#             drop_summary_list.append({app_name: drop_summary})

#     return drop_summary_list, start_time, end_time


@tool
def get_flow_summary(trace_id: int, timestamp: int, start_time: int, end_time: int) -> tuple[int,str]:

    """
    Get information of the flows captured by the trace. 

    Args:
        trace_id (int): Trace ID to retrieve the entry time from. 
        timestamp (int): Timestamp of trace
        start_time (int): Start time to filter query. This comes from get_aggregate_data function
        end_time (int): End time to filter query. This comes from get_aggregate_data function

    Returns:
       flow_summary (list): Summary information of the captured flows
    """

    return _get_flow_summary(trace_id, timestamp, start_time, end_time)

def _get_flow_summary(trace_id: int, timestamp: int, start_time: int, end_time: int) -> tuple[int,str]:

    url = "https://%s/dataservice/stream/device/nwpi/traceFinFlowWithQuery?traceId=%s&timestamp=%s"%(vmanage_host,trace_id,timestamp)

    start_time,end_time = calculate_times(timestamp)
    payload = json.dumps({
        "query": {
            "condition": "AND",
            "rules": [
                {
                    "value": [
                    start_time,
                    end_time
                    ],
                    "field": "data.received_timestamp",
                    "type": "date",
                    "operator": "greater"
                }
            ]
            }
        })

    response = requests.request("GET", url, headers=header, verify=False, data=payload)


    if response.status_code == 200:
        resp = response.json()
        print(resp, "flow summary")
        flows = resp["data"]
        flow_summary = []
        for flow in flows:
            flow_info = {
                "Flow ID:" : flow["data"]["flow_id"],
                "Device Trace ID:": flow["data"]["device_trace_id"],
                "Source:": flow["data"]["src_ip"], 
                "Destination:": flow["data"]["dst_ip"],
                "Application:": flow["data"]["app_name"],
                "Protocol:": flow["data"]["protocol"],
                }
            flow_summary.append(flow_info)
        return flow_summary
    

@tool
def get_flow_detail(device_trace_id: int, timestamp: int, flow_id: int) -> list[dict]:

    """
    Get detailed information of one flow on one trace. 

    Args:
        device_trace_id (int): Device Trace ID to retrieve the entry time from. 
        timestamp (int): Timestamp of trace
        flow_id (int): Flow number to get more information about. 

    Returns:
       flow_detail_summary (list[dict]): Detailed information of the captured flows
    """

    return _get_flow_detail(device_trace_id, timestamp, flow_id)

def _get_flow_detail(device_trace_id: int, timestamp: int, flow_id: int) -> list[dict]:

    url = "https://%s/dataservice/stream/device/nwpi/flowDetail?traceId=%s&timestamp=%s&flowId=%s"%(vmanage_host,device_trace_id,timestamp,flow_id)

    response = requests.request("GET", url, headers=header, verify=False)

    if response.status_code == 200:
        traces = response.json()
        print(traces, "flow details")
        flow_detail_summary = []
        upstream_list = []
        downstream_list=[]
        timestamps = []
        events = []
        features = []
        devices = []
        midpoint = len(traces) // 2
        for trace in traces[:midpoint]:
            if "received_timestamp" in trace["data"]:
                if trace["data"]["received_timestamp"] not in timestamps and trace["data"]["device_name"] not in devices:
                    timestamps.append(trace["data"]["received_timestamp"])
                    devices.append(trace["data"]["device_name"])
            else:
                if trace["data"]["packet_received_timestamp"] not in timestamps and trace["data"]["device_name"] not in devices:
                    timestamps.append(trace["data"]["packet_received_timestamp"])
                    devices.append(trace["data"]["device_name"])

        for timestamp in timestamps:
            for trace in traces:
                if "received_timestamp" in trace["data"]:
                    if timestamp == trace["data"]["received_timestamp"]:
                        events.append(trace)
                if "packet_received_timestamp" in trace["data"]:
                    if timestamp == trace["data"]["packet_received_timestamp"]:
                        features.append(trace)
        # print(events, "events")
        # print(features, "features")
        if len(events) > 0:
            for event in events[::-1]:
                for feature in features:
                    if event["data"]["event_direction"] == "upstream" and event["data"]["packet_id"] == feature["data"]["packet"]["packet_id"]:
                        upstream_list.append({   
                                        "Hop": event["data"]["device_name"],
                                        "Event": event["data"]["event_name"],
                                        "Local Color" : replace_invalid_color(event,"local_color"),
                                        "Remote Color": replace_invalid_color(event,"remote_color"),
                                        "Ingress Intf": get_feature_detail(feature,"ingress_fia","Ingress Report"),
                                        "Egress Intf": get_feature_detail(feature,"egress_fia","Transmit Report"),
                                        "Ingress Features": get_features_summary(feature, "ingress_fia"),
                                        "Egress Features": get_features_summary(feature, "egress_fia"),
                                        "Fwd decision based on": feature["data"]["packet"]["packet_fwd_decision"]
                                            })
                        
                    if event["data"]["event_direction"] == "downstream" and event["data"]["packet_id"] == feature["data"]["packet"]["packet_id"]:
                        downstream_list.append({
                                        "Hop": event["data"]["device_name"],
                                        "Event": event["data"]["event_name"],
                                        "Local Color" : replace_invalid_color(event,"local_color"),
                                        "Remote Color": replace_invalid_color(event,"remote_color"),
                                        "Ingress Intf": get_feature_detail(feature,"ingress_fia","Ingress Report"),
                                        "Egress Intf": get_feature_detail(feature,"egress_fia","Transmit Report"),
                                        "Ingress Features": get_features_summary(feature, "ingress_fia"),
                                        "Egress Features": get_features_summary(feature, "egress_fia"),
                                        "Fwd decision based on": feature["data"]["packet"]["packet_fwd_decision"]
                                        })
                
            flow_detail_summary.append({"Upstream": upstream_list,
                                    "Downstream" : list(reversed(downstream_list))})
            
            print(downstream_list, "downstream")
            print(flow_detail_summary, "flow summary")
        
        else:
            for feature in features:
                sdwan_fwd = find_value_path(feature["data"]["packet"]["packet"], "SDWAN Forwarding")
                key1 = sdwan_fwd[0]
                position = sdwan_fwd[1]
                key2 = "feature_detail"
                if find_direction(feature["data"]["packet"]["packet"][key1][position][key2]) == "upstream":
                    upstream_list.append({   
                                    "Hop": feature["data"]["device_name"],
                                    "Event": feature["data"]["packet"]["event_name"],
                                    "Local Color" : find_text(feature["data"]["packet"]["packet"][key1][position][key2],"local"),
                                    "Remote Color": find_text(feature["data"]["packet"]["packet"][key1][position][key2],"remote"),
                                    "Ingress Intf": get_feature_detail(feature,"ingress_fia","Ingress Report"),
                                    "Egress Intf": get_feature_detail(feature,"egress_fia","Transmit Report"),
                                    "Ingress Features": get_features_summary(feature, "ingress_fia"),
                                    "Egress Features": get_features_summary(feature, "egress_fia"),
                                    "Fwd decision based on": feature["data"]["packet"]["packet_fwd_decision"]
                                        })
                    
                if find_direction(feature["data"]["packet"]["packet"][key1][position][key2]) == "downstream":
                    downstream_list.append({
                                    "Hop": feature["data"]["device_name"],
                                    "Event": feature["data"]["packet"]["event_name"],
                                    "Local Color" : find_text(feature["data"]["packet"]["packet"][key1][position][key2],"local"),
                                    "Remote Color": find_text(feature["data"]["packet"]["packet"][key1][position][key2],"remote"),
                                    "Ingress Intf": get_feature_detail(feature,"ingress_fia","Ingress Report"),
                                    "Egress Intf": get_feature_detail(feature,"egress_fia","Transmit Report"),
                                    "Ingress Features": get_features_summary(feature, "ingress_fia"),
                                    "Egress Features": get_features_summary(feature, "egress_fia"),
                                    "Fwd decision based on": feature["data"]["packet"]["packet_fwd_decision"]
                                    })
            
        flow_detail_summary.append({"Upstream": upstream_list,
                                "Downstream" : list(reversed(downstream_list))})
        
        print(downstream_list, "downstream")
        print(flow_detail_summary, "flow summary")

        
        return flow_detail_summary

def find_direction(text) -> str:
    # Compile regular expressions to search for 'dir:Upstream' or 'dir:Downstream'
    upstream = re.compile(r'dir\s*:\s*Upstream', re.IGNORECASE)
    downstream = re.compile(r'dir\s*:\s*Downstream', re.IGNORECASE)


    if upstream.search(text):
        return "upstream"

    if downstream.search(text):
        return "downstream"
            
    # Return 'not found' if neither pattern is present
    return "not found"

def find_text(text, pattern) -> str:
    # Compile regular expressions to search for 'dir:Upstream' or 'dir:Downstream'
    local_color = re.compile(r'Local\s+Color\s*:\s*([\w-]+)', re.IGNORECASE)
    remote_color = re.compile(r'Remote\s+Color\s*:\s*([\w-]+)', re.IGNORECASE)
            
    if pattern == "local":
        match = local_color.search(text)
        if match:
            return match.group(1)
    
    if pattern == "remote":
        match = remote_color.search(text)
        if match:
            return match.group(1)
    
    # Return 'not found' if neither pattern is present
    return "not found"

def find_value_path(data, target):
    """
    Recursively find the path to the target value in a nested dictionary or list.

    Parameters:
    - data (dict or list): The nested data structure to search.
    - target: The value to find.

    Returns:
    - list: A list of keys and indices representing the path to the target value.
    """

    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, list) or isinstance(value, dict):
                # Recursively search for the target in nested structures
                path = find_value_path(value, target)
                if path is not None:
                    return [key] + path
            elif value == target:
                return [key]
    elif isinstance(data, list):
        for index, item in enumerate(data):
            if isinstance(item, dict) or isinstance(item, list):
                # Recursively search for the target in nested structures
                path = find_value_path(item, target)
                if path is not None:
                    return [index] + path
            elif item == target:
                return [index]
    return None


def get_feature_detail(json_data, direction, feature_name):
    if json_data["type"] == "feature-of-packet":
        for feature in json_data["data"]["packet"]['packet'][direction]:
            if feature['feature_name'] == feature_name:
                return feature['feature_detail']
    return

def get_features_summary(json_data, direction):
    if json_data["type"] == "feature-of-packet":
        features = []
        for feature in json_data["data"]["packet"]['packet'][direction]:
            features.append(feature["feature_name"])
        return features
    return

def replace_invalid_color(event, color_side):
    if event["data"]["event_direction"] == "upstream" and event["data"]["local_color"] == "INVALID" and event["data"]["remote_color"] == "INVALID":
        if color_side == "local_color":
            return "Service LAN"
        if color_side  == "remote_color":
            return "N/A"
    if event["data"]["event_direction"] == "downstream" and event["data"]["local_color"] == "INVALID" and event["data"]["remote_color"] == "INVALID":
        if color_side == "local_color":
            return "N/A"
        if color_side  == "remote_color":
            return "Service LAN"
    return event["data"][color_side]

def calculate_times(epoch_ms):
    # Convert the epoch time from milliseconds to seconds for compatibility
    epoch_sec = epoch_ms / 1000.0
    
    # Create a datetime object from the epoch time
    original_time = datetime.fromtimestamp(epoch_sec)
    
    # Calculate the time plus one minute and one hour
    time_plus_1_minute = original_time + timedelta(minutes=1)
    time_plus_1_hour = original_time + timedelta(hours=1)
    
    # Convert the datetime objects back to epoch time in milliseconds
    epoch_plus_1_minute_ms = int(time_plus_1_minute.timestamp() * 1000)
    epoch_plus_1_hour_ms = int(time_plus_1_hour.timestamp() * 1000)

    return epoch_plus_1_minute_ms, epoch_plus_1_hour_ms

@tool
def tracer_wait():
    """
    This is a function to sleep for 90 seconds. Useful when we are waiting for flows to be captured. 
    """
    return time.sleep(60)

@tool
def reviewer_wait():
    """
    This is a function to sleep for 5 seconds. Useful when we are waiting for flows to be captured. 
    """
    return time.sleep(5)