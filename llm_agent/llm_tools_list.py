from nwpi import(
    start_trace,
    get_site_list,
    get_device_details_from_site,
    trace_readout,
    get_entry_time_and_state,
    get_flow_summary,
    get_flow_detail,
    reviewer_wait,
    tracer_wait,

)
nwpi_tools = [
    start_trace,
    get_site_list,
    trace_readout,
    get_device_details_from_site,
    get_entry_time_and_state,
    get_flow_summary,
    get_flow_detail,
    tracer_wait,
]
reviewer_tools = [
    reviewer_wait,
]