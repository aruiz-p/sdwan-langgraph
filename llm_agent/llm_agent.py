# from langchain.chat_models import ChatOpenAI
from pydantic import ValidationError
from langchain_openai import ChatOpenAI
from langchain.agents import AgentExecutor
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage
from typing import Annotated, TypedDict
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.tools.render import format_tool_to_openai_function
from langchain.agents.output_parsers import OpenAIFunctionsAgentOutputParser
from langchain.agents.format_scratchpad import (
    format_to_openai_function_messages,
)

from llm_tools_list import reviewer_tools, nwpi_tools
from logging_config.main import setup_logging
from utils.text_utils import remove_white_spaces
from langchain_core.output_parsers.openai_functions import JsonOutputFunctionsParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import END, StateGraph, START
from typing import Sequence, TypedDict
from langchain.memory import ConversationBufferMemory
from typing import Annotated
import operator
import functools
from IPython.display import Image, display

logger = setup_logging()


NOTIFICATION_PROMPT = """
This is a network alert, not a user message.
"""

SUPERVISOR_TEMPLATE = """
You need to make sure that whatever is passed to the user makes sense, based on what they asked for. If the tracer
agent ask questions, defer to the reviewer to answer and pass the answer along. Don't go back to the user just saying that the trace has started,
You cannot go back to the user without going through the reviewer.
"""

REVIEWER_PROMPT = """
You are a professional reviewer. Your job is to make sure that the information passed to the corresponds with what was asked.
You will get questions from the tracer agent, try to answer them to the best of your abilities using the user input as guidance.
If the tracer is providing only information about the flow summary, you need to pick a specific flow to get the details from, based on the users input.
Pass alon the information you receive and your own conclusion. If you think there is a problem, suggest steps to fix it. 
"""

TRACER_PROMPT = """
You are a Cisco SD-WAN expert AI assistant, your role is to start Network Wide Path Insight traces on behalf of users to spot network issues. Follow these guidelines:
1.The user will let you know the site and vpn to start the trace. Additionally they could provide source and destination subnets.
2.Use the 'get_site_list' function to obtain the list of available sites to run the trace and confirm it matches with the user input.
3.Before starting the trace, use the 'get_device_details_from_site' to retrieve the device list that will be used as parameter.
4.Use the VPN, site id and source and destination networks provided by the user as parameters to start the trace.
5.After starting a trace, use the tracer_wait tool before checking if there are any flows captured. 
6.Verify if there are any flows and if there is any reported event. Use the trace_readout and get_flow_summary tools.
7.Get the "device_trace_id" with "get_device_trace_id" if it doesn't match the "trace_id" use it, otherwise use the "trace_id" value. 
7.Provide details of a flow that corresponds to what the user is asking for, use the get_flow_detail tool.
8.If the flow_detail is empty, try using a different flow id.
9.When user request information of a trace, always use "get_entry_time_and_state" to retrieve the entry_time and state, use it to get other information.
10.Even If the trace is already stopped, you can still provide information to the user about the captured summary flows.
11.If the state indicates an issue, you should still try to provide the user with the information requested.
12.To present the flow summary use one row for each flow.
13.Must use as much as possible emojis that are relevant to your messages to make them more human-friendly.
"""

MEMORY_KEY = "chat_history"

def create_agent(llm: ChatOpenAI, tools: list, system_prompt: str):
    # Each worker node will be given a name and some tools.
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                system_prompt,
            ),
            MessagesPlaceholder(variable_name=MEMORY_KEY),
            ("user","{input}"),
            MessagesPlaceholder(variable_name="agent_scratchpad"),
        ]
    )
    # agent = create_openai_tools_agent(llm, tools, prompt)
    llm_with_tools = llm.bind(
        functions=[format_tool_to_openai_function(t) for t in tools]
    )
    agent = (
        {
            "input": lambda x: x["input"],
            "agent_scratchpad": lambda x: format_to_openai_function_messages(
                x.get("intermediate_steps",[])
            ),
            "chat_history": lambda x: x.get("chat_history",[]),
        }
        | prompt
        | llm_with_tools
        | OpenAIFunctionsAgentOutputParser()
    )
    memory = ConversationBufferMemory(
            memory_key="chat_history", return_messages=True
        )
    
    executor = AgentExecutor(agent=agent, tools=tools, memory=memory)
    return executor

def agent_node(state, agent, name):
    result = agent.invoke({"input": state["input"][-1].content})
    output_message = result["output"]  # Extract the output message
    
    return {
        "input": state["input"] + [HumanMessage(content=output_message, name=name)],
    }

members = ["Tracer", "Reviewer"]
system_prompt = (
    "You are a supervisor tasked with managing a conversation between the"
    " following workers:  {members}. Given the following user request,"
    " respond with the worker to act next. Each worker will perform a"
    " task and respond with their results and status. When finished,"
    " respond with FINISH."
)
# Our team supervisor is an LLM node. It just picks the next agent to process
# and decides when the work is completed
options = ["FINISH"] + members
# Using openai function calling can make output parsing easier for us
function_def = {
    "name": "route",
    "description": "Select the next role.",
    "parameters": {
        "title": "routeSchema",
        "type": "object",
        "properties": {
            "next": {
                "title": "Next",
                "anyOf": [
                    {"enum": options},
                ],
            }
        },
        "required": ["next"],
    },
}
prompt = ChatPromptTemplate.from_messages(
    [
        ("system", remove_white_spaces(system_prompt)),
        MessagesPlaceholder(variable_name="input"),
        (
            "system",
            "Given the conversation above, who should act next?"
            " Or should we FINISH? Select one of: {options}",
        ),
    ]
).partial(options=str(options), members=", ".join(members))

llm = ChatOpenAI(model="gpt-4o-mini")

supervisor_chain = (
    prompt
    | llm.bind_functions(functions=[function_def], function_call="route")
    | JsonOutputFunctionsParser()
)

# The agent state is the input to each node in the graph
class AgentState(TypedDict):
    # The annotation tells the graph that new messages will always
    # be added to the current states
    input: Annotated[Sequence[BaseMessage], operator.add]
    # The 'next' field indicates where to route to next
    next: str

def create_agent_graph() -> StateGraph:
    tracer_agent = create_agent(llm, nwpi_tools, remove_white_spaces(TRACER_PROMPT))
    tracer_node = functools.partial(agent_node, agent=tracer_agent, name="Tracer")
    reviewer_agent = create_agent(llm, reviewer_tools, remove_white_spaces(REVIEWER_PROMPT))
    reviewer_node = functools.partial(agent_node, agent=reviewer_agent, name="Reviewer")

    workflow = StateGraph(AgentState)
    workflow.add_node("Tracer", tracer_node)
    workflow.add_node("Reviewer", reviewer_node)
    workflow.add_node("supervisor", supervisor_chain)


    for member in members:
        # We want our workers to ALWAYS "report back" to the supervisor when done
        workflow.add_edge(member, "supervisor")

    conditional_map = {k: k for k in members}
    conditional_map["FINISH"] = END
    workflow.add_conditional_edges("supervisor", lambda x: x["next"], conditional_map)
    #Add entrypoint
    workflow.add_edge(START, "supervisor")

    graph = workflow.compile()
   
    graph.get_graph(xray=True).draw_mermaid_png(output_file_path="output_xray sec.png")

    return graph


if __name__ == "__main__":
    # agent = create_agent_graph()
    # chat = agent.chat(
    #     "please provide a summary of all activities I asked you to check in our conversation"
    # )
    # print(chat)
    print("#" * 80, "\n")