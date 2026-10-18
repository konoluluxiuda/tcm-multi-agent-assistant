"""
Agent Decision System for Multi-Agent Medical Chatbot

This module handles the orchestration of different agents using LangGraph.
It dynamically routes user queries to the appropriate agent based on content and context.
"""

import json
import atexit
from agents.runtime import session_turn, SharedRAG
from typing import Dict, List, Optional, Any, Literal, TypedDict, Union, Annotated
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage, RemoveMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.runnables import RunnablePassthrough
from langgraph.graph import MessagesState, StateGraph, END
import os, getpass
from agents.guardrails.local_guardrails import LocalGuardrails

from agents.conversation_store import ConversationStore

import threading

from config import Config
from tcm.chat import HerbContext, parse_request, execute_turn
from tcm.agent_research import parse_request as parse_research_request, runtime as research_runtime
from tcm.agent_research import split_contexts, pack_contexts
from tcm.agent_pubmed import parse_request as parse_pubmed_request, runtime as pubmed_runtime, context_from as pubmed_context_from
from tcm.assistant_router import resolve as resolve_tool_request
from tcm.assistant_info import execute as execute_assistant_info


# Load configuration
config = Config()

# Completed-turn snapshots survive application restarts.
memory = ConversationStore.from_env()

# One owner for the local vector DB. Threads serialize RAG work in this process.
rag_runtime = SharedRAG()
atexit.register(rag_runtime.close)


# Agent that takes the decision of routing the request further to correct task specific agent
def MedicalRAG(configuration):
    # Preserve the injectable factory used by eval_rag / compare_rag.
    from agents.rag_agent import MedicalRAG as RAG
    return RAG(configuration)


class AgentConfig:
    """Configuration settings for the agent decision system."""
    
    # Decision model
    DECISION_MODEL = "gpt-4o"  # or whichever model you prefer
    
    # Vision model for image analysis
    VISION_MODEL = "gpt-4o"
    
    # Confidence threshold for responses
    CONFIDENCE_THRESHOLD = 0.85
    
    # System instructions for the decision agent
    DECISION_SYSTEM_PROMPT = """You are an intelligent medical triage system that routes user queries to 
    the appropriate specialized agent. Your job is to analyze the user's request and determine which agent 
    is best suited to handle it based on the query content, presence of images, and conversation context.

    Available agents:
    1. CONVERSATION_AGENT - For general chat, greetings, and non-medical questions.
    2. RAG_AGENT - For specific medical knowledge questions that can be answered from established medical literature. Currently ingested medical knowledge involves 'introduction to brain tumor', 'deep learning techniques to diagnose and detect brain tumors', 'deep learning techniques to diagnose and detect covid / covid-19 from chest x-ray'.
    3. WEB_SEARCH_PROCESSOR_AGENT - For questions about recent medical developments, current outbreaks, or time-sensitive medical information.
    4. BRAIN_TUMOR_AGENT - For analysis of brain MRI images to detect and segment tumors.
    5. CHEST_XRAY_AGENT - For analysis of chest X-ray images to detect abnormalities.
    6. SKIN_LESION_AGENT - For analysis of skin lesion images to classify them as benign or malignant.

    Make your decision based on these guidelines:
    - When no image is uploaded, select conversation, RAG, or enabled web search according to the text request.
    - If the user uploads a medical image, decide which medical vision agent is appropriate based on the image type and the user's query. If the image is uploaded without a query, always route to the correct medical vision agent based on the image type.
    - If the user asks about recent medical developments or current health situations, use the web search pocessor agent.
    - If the user asks specific medical knowledge questions, use the RAG agent.
    - For general conversation, greetings, or non-medical questions, use the conversation agent. But if image is uploaded, always go to the medical vision agents first.

    You must provide your answer in JSON format with the following structure:
    {{
    "agent": "AGENT_NAME",
    "reasoning": "Your step-by-step reasoning for selecting this agent",
    "confidence": 0.95  // Value between 0.0 and 1.0 indicating your confidence in this decision
    }}
    """

    image_analyzer = None
    image_lock = threading.Lock()

    @classmethod
    def get_image_analyzer(cls):
        if not config.features.image_analysis:
            raise ValueError('Image analysis is disabled')
        with cls.image_lock:
            if cls.image_analyzer is None:
                from agents.image_analysis_agent import ImageAnalysisAgent
                cls.image_analyzer = ImageAnalysisAgent(config=config)
        return cls.image_analyzer


class AgentState(MessagesState):
    """State maintained across the workflow."""
    # messages: List[BaseMessage]  # Conversation history
    agent_name: Optional[str]  # Current active agent
    current_input: Optional[Union[str, Dict]]  # Input to be processed
    has_image: bool  # Whether the current input contains an image
    image_type: Optional[str]  # Type of medical image if present
    output: Optional[str]  # Final output to user
    needs_human_validation: bool  # Whether human validation is required
    retrieval_confidence: float  # Confidence in retrieval (for RAG agent)
    bypass_routing: bool  # Flag to bypass agent routing for guardrails
    insufficient_info: bool  # Flag indicating RAG response has insufficient information
    next: str  # Explicit graph branch, rather than an undeclared nested state
    disabled_feature: Optional[str]
    tcm_request: Optional[Dict]
    tcm_result: Optional[Dict]
    tcm_context: Optional[HerbContext]
    research_request: Optional[Dict]
    research_result: Optional[Dict]
    research_context: Optional[Dict]
    pubmed_request: Optional[Dict]
    pubmed_result: Optional[Dict]
    pubmed_context: Optional[Dict]
    assistant_request: Optional[Dict]
    assistant_result: Optional[Dict]
    routing: Optional[Dict]


class AgentDecision(TypedDict):
    """Output structure for the decision agent."""
    agent: str
    reasoning: str
    confidence: float


def create_agent_graph():
    """Create and configure the LangGraph for agent orchestration."""

    # Initialize guardrails with the same LLM used elsewhere
    guardrails = LocalGuardrails(config.rag.llm)

    # LLM
    decision_model = config.agent_decision.llm
    
    # Initialize the output parser
    json_parser = JsonOutputParser(pydantic_object=AgentDecision)
    
    # Create the decision prompt
    available_agents = ['CONVERSATION_AGENT', 'RAG_AGENT']
    if config.features.state('web_search')['available']:
        available_agents.append('WEB_SEARCH_PROCESSOR_AGENT')
    if config.features.image_analysis:
        available_agents.extend(['BRAIN_TUMOR_AGENT','CHEST_XRAY_AGENT','SKIN_LESION_AGENT'])
    system_prompt = AgentConfig.DECISION_SYSTEM_PROMPT + '\nOnly select currently enabled agents: ' + ', '.join(available_agents)
    decision_prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{input}")
    ])
    
    # Create the decision chain
    decision_chain = decision_prompt | decision_model | json_parser
    
    # Define graph state transformations
    def analyze_input(state: AgentState) -> AgentState:
        """Analyze the input to detect images and determine input type."""
        current_input = state["current_input"]
        has_image = False
        image_type = None
        
        # Get the text from the input
        input_text = ""
        if isinstance(current_input, str):
            input_text = current_input
        elif isinstance(current_input, dict):
            input_text = current_input.get("text", "")
        
        selected = resolve_tool_request(current_input, herb_context=state.get('tcm_context'),
                                        research_context=state.get('research_context'),
                                        pubmed_context=state.get('pubmed_context'))
        if selected is not None:
            tool = selected['tool']
            key = {'herb': 'tcm_request', 'research': 'research_request',
                   'pubmed': 'pubmed_request', 'assistant': 'assistant_request'}[tool]
            changes = {key: selected['request'], 'has_image': False, 'image_type': None,
                       'bypass_routing': False, 'routing': {k: selected[k] for k in ('tool','reason','mode')}}
            if tool != 'assistant':
                for owner, context_key in [('herb','tcm_context'),('research','research_context'),('pubmed','pubmed_context')]:
                    if owner != tool:
                        changes[context_key] = None
            return changes
        state = {**state, 'research_context': None, 'pubmed_context': None}

        # An unrelated turn ends the explicit herb focus, even if a guardrail blocks it.
        state = {**state, "tcm_context": None}

        # Check input through guardrails if text is present
        if input_text:
            is_allowed, message = guardrails.check_input(input_text)
            if not is_allowed:
                # If input is blocked, return early with guardrail message
                print(f"Selected agent: INPUT GUARDRAILS, Message: ", message)
                return {
                    **state,
                    "messages": message,
                    "agent_name": "INPUT_GUARDRAILS",
                    "has_image": False,
                    "image_type": None,
                    "bypass_routing": True  # flag to end flow
                }
        
        # Original image processing code
        if isinstance(current_input, dict) and "image" in current_input:
            if not config.features.image_analysis:
                return {'has_image': False, 'image_type': None, 'bypass_routing': False,
                        'disabled_feature': 'image_analysis', 'tcm_context': None}
            has_image = True
            image_path = current_input.get("image", None)
            image_type_response = AgentConfig.get_image_analyzer().analyze_image(image_path)
            image_type = image_type_response['image_type']
            print("ANALYZED IMAGE TYPE: ", image_type)
        
        return {
            **state,
            "has_image": has_image,
            "image_type": image_type,
            "bypass_routing": False  # Explicitly set to False for normal flow
        }
    
    def check_if_bypassing(state: AgentState) -> str:
        """Check if we should bypass normal routing due to guardrails."""
        if state.get('assistant_request') is not None:
            return 'ASSISTANT_INFO_AGENT'
        if state.get('pubmed_request') is not None:
            return 'PUBMED_AGENT'
        if state.get('research_request') is not None:
            return 'PHARMACOPOEIA_RESEARCH_AGENT'
        if state.get("tcm_request") is not None:
            return "TCM_HERB_AGENT"
        if state.get("disabled_feature"):
            return "feature_unavailable"
        if state.get("bypass_routing", False):
            return "apply_guardrails"
        return "route_to_agent"
    
    def run_assistant_info_agent(state: AgentState) -> Dict:
        result = execute_assistant_info(state['assistant_request'])
        message = AIMessage(content=result['message'])
        return {'assistant_result': result, 'agent_name': 'ASSISTANT_INFO_AGENT',
                'output': message, 'messages': [message], 'needs_human_validation': False}

    def run_tcm_herb_agent(state: AgentState) -> Dict:
        result, context = execute_turn(state["tcm_request"], state.get("tcm_context"))
        message = AIMessage(content=result["message"])
        return {"tcm_result": result, "tcm_context": context, "agent_name": "TCM_HERB_AGENT",
                "output": message, "messages": [message], "needs_human_validation": False}

    def run_research_agent(state: AgentState) -> Dict:
        result, context = research_runtime.execute(state['research_request'], state.get('research_context'))
        message = AIMessage(content=result['message'])
        return {'research_result': result, 'research_context': context, 'tcm_context': None,
                'agent_name': 'PHARMACOPOEIA_RESEARCH_AGENT', 'output': message,
                'messages': [message], 'needs_human_validation': False}

    def run_pubmed_agent(state: AgentState) -> Dict:
        result, context = pubmed_runtime.execute(state['pubmed_request'], state.get('pubmed_context'))
        message = AIMessage(content=result['message'])
        return {'pubmed_result': result, 'pubmed_context': context, 'tcm_context': None, 'research_context': None,
                'agent_name': 'PUBMED_AGENT', 'output': message, 'messages': [message], 'needs_human_validation': False}

    def route_to_agent(state: AgentState) -> Dict:
        """Make decision about which agent should handle the query."""
        messages = state["messages"]
        current_input = state["current_input"]
        has_image = state["has_image"]
        image_type = state["image_type"]
        
        # Prepare input for decision model
        input_text = ""
        if isinstance(current_input, str):
            input_text = current_input
        elif isinstance(current_input, dict):
            input_text = current_input.get("text", "")
        
        # Create context from recent conversation history (last 3 messages)
        recent_context = ""
        for msg in messages[-6:]:  # Get last 3 exchanges (6 messages)  # Not provided control from config
            if isinstance(msg, HumanMessage):
                recent_context += f"User: {msg.content}\n"
            elif isinstance(msg, AIMessage):
                recent_context += f"Assistant: {msg.content}\n"
        
        # Combine everything for the decision input
        decision_input = f"""
        User query: {input_text}

        Recent conversation context:
        {recent_context}

        Has image: {has_image}
        Image type: {image_type if has_image else 'None'}

        Based on this information, which agent should handle this query?
        """
        
        # Make the decision
        decision = decision_chain.invoke({"input": decision_input})

        # Decided agent
        print(f"Decision: {decision['agent']}")
        
        agent = decision['agent']
        image_agents = {'BRAIN_TUMOR_AGENT', 'CHEST_XRAY_AGENT', 'SKIN_LESION_AGENT'}
        if agent in image_agents and not config.features.image_analysis:
            return {'agent_name': agent, 'next': 'feature_unavailable', 'disabled_feature': 'image_analysis'}
        if agent == 'WEB_SEARCH_PROCESSOR_AGENT' and not config.features.state('web_search')['available']:
            return {'agent_name': agent, 'next': 'feature_unavailable', 'disabled_feature': 'web_search'}
        if decision['confidence'] < AgentConfig.CONFIDENCE_THRESHOLD:
            return {'agent_name': agent, 'next': 'needs_validation'}
        return {'agent_name': agent, 'next': agent}

    def feature_unavailable(state):
        feature = state.get('disabled_feature') or 'web_search'
        reason = config.features.state(feature)['reason']
        message = ('图像分析功能已关闭，请使用文本查询。' if feature == 'image_analysis' else
                   '本地资料或当前能力不足以完成该请求；联网检索已关闭。' if reason == 'disabled' else
                   '本地资料或当前能力不足以完成该请求；联网检索未配置密钥。')
        output = AIMessage(content=message)
        return {'output': output, 'messages': [output], 'agent_name': 'FEATURE_UNAVAILABLE',
                'needs_human_validation': False}

    # Define agent execution functions (these will be implemented in their respective modules)
    def run_conversation_agent(state: AgentState) -> AgentState:
        """Handle general conversation."""

        print(f"Selected agent: CONVERSATION_AGENT")

        messages = state["messages"]
        current_input = state["current_input"]
        
        # Prepare input for decision model
        input_text = ""
        if isinstance(current_input, str):
            input_text = current_input
        elif isinstance(current_input, dict):
            input_text = current_input.get("text", "")
        
        # Create context from recent conversation history
        recent_context = ""
        for msg in messages:#[-20:]:  # Get last 10 exchanges (20 messages)  # currently considering complete history - limit control from config
            if isinstance(msg, HumanMessage):
                # print("######### DEBUG 1:", msg)
                recent_context += f"User: {msg.content}\n"
            elif isinstance(msg, AIMessage):
                # print("######### DEBUG 2:", msg)
                recent_context += f"Assistant: {msg.content}\n"
        
        # Combine everything for the decision input
        conversation_prompt = f"""User query: {input_text}

        Recent conversation context: {recent_context}

        You are an AI-powered Medical Conversation Assistant. Your goal is to facilitate smooth and informative conversations with users, handling both casual and medical-related queries. You must respond naturally while ensuring medical accuracy and clarity.

        ### Role & Capabilities
        - Engage in **general conversation** while maintaining professionalism.
        - Answer **medical questions** using verified knowledge.
        - Route **complex queries** to RAG (retrieval-augmented generation) or web search if needed.
        - Handle **follow-up questions** while keeping track of conversation context.
        - Redirect **medical images** to the appropriate AI analysis agent.

        ### Guidelines for Responding:
        1. **General Conversations:**
        - If the user engages in casual talk (e.g., greetings, small talk), respond in a friendly, engaging manner.
        - Keep responses **concise and engaging**, unless a detailed answer is needed.

        2. **Medical Questions:**
        - If you have **high confidence** in answering, provide a medically accurate response.
        - Ensure responses are **clear, concise, and factual**.

        3. **Follow-Up & Clarifications:**
        - Maintain conversation history for better responses.
        - If a query is unclear, ask **follow-up questions** before answering.

        4. **Handling Medical Image Analysis:**
        - Do **not** attempt to analyze images yourself.
        - If user speaks about analyzing or processing or detecting or segmenting or classifying any disease from any image, ask the user to upload the image so that in the next turn it is routed to the appropriate medical vision agents.
        - If an image was uploaded, it would have been routed to the medical computer vision agents. Read the history to know about the diagnosis results and continue conversation if user asks anything regarding the diagnosis.
        - After processing, **help the user interpret the results**.

        5. **Uncertainty & Ethical Considerations:**
        - If unsure, **never assume** medical facts.
        - Recommend consulting a **licensed healthcare professional** for serious medical concerns.
        - Avoid providing **medical diagnoses** or **prescriptions**—stick to general knowledge.

        ### Response Format:
        - Maintain a **conversational yet professional tone**.
        - Use **bullet points or numbered lists** for clarity when needed.
        - If pulling from external sources (RAG/Web Search), mention **where the information is from** (e.g., "According to Mayo Clinic...").
        - If a user asks for a diagnosis, remind them to **seek medical consultation**.

        ### Example User Queries & Responses:

        **User:** "Hey, how's your day going?"
        **You:** "I'm here and ready to help! How can I assist you today?"

        **User:** "I have a headache and fever. What should I do?"
        **You:** "I'm not a doctor, but headaches and fever can have various causes, from infections to dehydration. If your symptoms persist, you should see a medical professional."

        Conversational LLM Response:"""

        # print("Conversation Prompt:", conversation_prompt)

        if not config.features.image_analysis:
            conversation_prompt += '\nImage analysis is disabled; do not ask for an image upload.'
        response = config.conversation.llm.invoke(conversation_prompt)

        # print("Conversation respone:", response)

        # response = AIMessage(content="This would be handled by the conversation agent.")

        return {
            **state,
            "output": response,
            "agent_name": "CONVERSATION_AGENT"
        }
    
    def run_rag_agent(state: AgentState) -> AgentState:
        """Handle medical knowledge queries using RAG."""
        # Initialize the RAG agent

        print(f"Selected agent: RAG_AGENT")

        
        messages = state["messages"]
        query = state["current_input"]
        rag_context_limit = config.rag.context_limit

        recent_context = ""
        for msg in messages[-rag_context_limit:]:# limit controlled from config
            if isinstance(msg, HumanMessage):
                # print("######### DEBUG 1:", msg)
                recent_context += f"User: {msg.content}\n"
            elif isinstance(msg, AIMessage):
                # print("######### DEBUG 2:", msg)
                recent_context += f"Assistant: {msg.content}\n"

        response = rag_runtime.query(MedicalRAG, config, query, recent_context)
        retrieval_confidence = response.get("confidence", 0.0)  # Default to 0.0 if not provided

        print(f"Retrieval Confidence: {retrieval_confidence}")
        print(f"Sources: {len(response['sources'])}")

        # Check if response indicates insufficient information
        insufficient_info = False
        response_content = response["response"]
        
        # Extract the content properly based on type
        if isinstance(response_content, dict) and hasattr(response_content, 'content'):
            # If it's an AIMessage or similar object with a content attribute
            response_text = response_content.content
        else:
            # If it's already a string
            response_text = response_content
            
        print(f"Response text type: {type(response_text)}")
        print(f"Response text preview: {response_text[:100]}...")
        
        if isinstance(response_text, str) and (
            "I don't have enough information to answer this question based on the provided context" in response_text or 
            "I don't have enough information" in response_text or 
            "don't have enough information" in response_text.lower() or
            "not enough information" in response_text.lower() or
            "insufficient information" in response_text.lower() or
            "cannot answer" in response_text.lower() or
            "unable to answer" in response_text.lower()
            ):
            
            print("RAG response indicates insufficient information")
            print(f"Response text that triggered insufficient_info: {response_text[:100]}...")
            insufficient_info = True

        print(f"Insufficient info flag set to: {insufficient_info}")

        # Store RAG output ONLY if confidence is high
        if retrieval_confidence >= config.rag.min_retrieval_confidence:
            # response_output = response["response"]
            response_output = AIMessage(content=response_text)
        else:
            response_output = AIMessage(content="")
        
        return {
            **state,
            "output": response_output,
            "needs_human_validation": False,  # Assuming no validation needed for RAG responses
            "retrieval_confidence": retrieval_confidence,
            "agent_name": "RAG_AGENT",
            "insufficient_info": insufficient_info
        }

    # Web Search Processor Node
    def run_web_search_processor_agent(state: AgentState) -> AgentState:
        """Handles web search results, processes them with LLM, and generates a refined response."""
        if not config.features.state('web_search')['available']:
            return feature_unavailable(state)


        print(f"Selected agent: WEB_SEARCH_PROCESSOR_AGENT")
        print("[WEB_SEARCH_PROCESSOR_AGENT] Processing Web Search Results...")
        
        messages = state["messages"]
        web_search_context_limit = config.web_search.context_limit

        recent_context = ""
        for msg in messages[-web_search_context_limit:]: # limit controlled from config
            if isinstance(msg, HumanMessage):
                # print("######### DEBUG 1:", msg)
                recent_context += f"User: {msg.content}\n"
            elif isinstance(msg, AIMessage):
                # print("######### DEBUG 2:", msg)
                recent_context += f"Assistant: {msg.content}\n"

        from agents.web_search_processor_agent import WebSearchProcessorAgent
        web_search_processor = WebSearchProcessorAgent(config)

        processed_response = web_search_processor.process_web_search_results(query=state["current_input"], chat_history=recent_context)

        # print("######### DEBUG WEB SEARCH:", processed_response)
        
        if state['agent_name'] != None:
            involved_agents = f"{state['agent_name']}, WEB_SEARCH_PROCESSOR_AGENT"
        else:
            involved_agents = "WEB_SEARCH_PROCESSOR_AGENT"

        # Overwrite any previous output with the processed Web Search response
        return {
            **state,
            # "output": "This would be handled by the web search agent, finding the latest information.",
            "output": processed_response,
            "agent_name": involved_agents
        }

    # Define Routing Logic
    def confidence_based_routing(state: AgentState) -> Dict[str, str]:
        """Route based on RAG confidence score and response content."""
        # Debug prints
        print(f"Routing check - Retrieval confidence: {state.get('retrieval_confidence', 0.0)}")
        print(f"Routing check - Insufficient info flag: {state.get('insufficient_info', False)}")
        
        # Redirect if confidence is low or if response indicates insufficient info
        if (state.get("retrieval_confidence", 0.0) < config.rag.min_retrieval_confidence or 
            state.get("insufficient_info", False)):
            print("Re-routed to Web Search Agent due to low confidence or insufficient information...")
            return "WEB_SEARCH_PROCESSOR_AGENT" if config.features.state("web_search")["available"] else "feature_unavailable"
        return "check_validation"  # No transition needed if confidence is high and info is sufficient
    
    def run_brain_tumor_agent(state: AgentState) -> AgentState:
        """Handle brain MRI image analysis."""

        print(f"Selected agent: BRAIN_TUMOR_AGENT")

        response = AIMessage(content="This would be handled by the brain tumor agent, analyzing the MRI image.")

        return {
            **state,
            "output": response,
            "needs_human_validation": True,  # Medical diagnosis always needs validation
            "agent_name": "BRAIN_TUMOR_AGENT"
        }
    
    def run_chest_xray_agent(state: AgentState) -> AgentState:
        """Handle chest X-ray image analysis."""

        current_input = state["current_input"]
        image_path = current_input.get("image", None)

        print(f"Selected agent: CHEST_XRAY_AGENT")

        # classify chest x-ray into covid or normal
        predicted_class = AgentConfig.get_image_analyzer().classify_chest_xray(image_path)

        if predicted_class == "covid19":
            response = AIMessage(content="The analysis of the uploaded chest X-ray image indicates a **POSITIVE** result for **COVID-19**.")
        elif predicted_class == "normal":
            response = AIMessage(content="The analysis of the uploaded chest X-ray image indicates a **NEGATIVE** result for **COVID-19**, i.e., **NORMAL**.")
        else:
            response = AIMessage(content="The uploaded image is not clear enough to make a diagnosis / the image is not a medical image.")

        # response = AIMessage(content="This would be handled by the chest X-ray agent, analyzing the image.")

        return {
            **state,
            "output": response,
            "needs_human_validation": True,  # Medical diagnosis always needs validation
            "agent_name": "CHEST_XRAY_AGENT"
        }
    
    def run_skin_lesion_agent(state: AgentState) -> AgentState:
        """Handle skin lesion image analysis."""

        current_input = state["current_input"]
        image_path = current_input.get("image", None)

        print(f"Selected agent: SKIN_LESION_AGENT")

        # classify chest x-ray into covid or normal
        predicted_mask = AgentConfig.get_image_analyzer().segment_skin_lesion(image_path)

        if predicted_mask:
            response = AIMessage(content="Following is the analyzed **segmented** output of the uploaded skin lesion image:")
        else:
            response = AIMessage(content="The uploaded image is not clear enough to make a diagnosis / the image is not a medical image.")

        # response = AIMessage(content="This would be handled by the skin lesion agent, analyzing the skin image.")

        return {
            **state,
            "output": response,
            "needs_human_validation": True,  # Medical diagnosis always needs validation
            "agent_name": "SKIN_LESION_AGENT"
        }
    
    def handle_human_validation(state: AgentState) -> Dict:
        """Prepare for human validation if needed."""
        if state.get("needs_human_validation", False):
            return {"next": "human_validation"}
        return {"next": END}
    
    def perform_human_validation(state: AgentState) -> AgentState:
        """Handle human validation process."""
        print(f"Selected agent: HUMAN_VALIDATION")

        # Append validation request to the existing output
        validation_prompt = f"{state['output'].content}\n\n**Human Validation Required:**\n- If you're a healthcare professional: Please validate the output. Select **Yes** or **No**. If No, provide comments.\n- If you're a patient: Simply click Yes to confirm."

        # Create an AI message with the validation prompt
        validation_message = AIMessage(content=validation_prompt)

        return {
            **state,
            "output": validation_message,
            "agent_name": f"{state['agent_name']}, HUMAN_VALIDATION"
        }

    # Check output through guardrails
    def apply_output_guardrails(state: AgentState) -> AgentState:
        """Apply output guardrails to the generated response."""
        output = state["output"]
        current_input = state["current_input"]

        # Check if output is valid
        if not output or not isinstance(output, (str, AIMessage)):
            return state

        output_text = output if isinstance(output, str) else output.content
        
        # If the last message was a human validation message
        if "Human Validation Required" in output_text:
            # Check if the current input is a human validation response
            validation_input = ""
            if isinstance(current_input, str):
                validation_input = current_input
            elif isinstance(current_input, dict):
                validation_input = current_input.get("text", "")
            
            # If validation input exists
            if validation_input.lower().startswith(('yes', 'no')):
                # Add the validation result to the conversation history
                validation_response = HumanMessage(content=f"Validation Result: {validation_input}")
                
                # If validation is 'No', modify the output
                if validation_input.lower().startswith('no'):
                    fallback_message = AIMessage(content="The previous medical analysis requires further review. A healthcare professional has flagged potential inaccuracies.")
                    return {
                        **state,
                        "messages": [validation_response, fallback_message],
                        "output": fallback_message
                    }
                
                return {
                    **state,
                    "messages": validation_response
                }
        
        # Get the original input text
        input_text = ""
        if isinstance(current_input, str):
            input_text = current_input
        elif isinstance(current_input, dict):
            input_text = current_input.get("text", "")
        
        # Apply output sanitization
        history = "\n".join(
            f"{msg.type}: {msg.content}"
            for msg in state["messages"][-config.max_conversation_history:]
        )
        sanitized_output = guardrails.check_output(output_text, input_text, chat_history=history)
        # sanitized_output = output_text
        
        # For non-validation cases, add the sanitized output to messages
        sanitized_message = AIMessage(content=sanitized_output) if isinstance(output, AIMessage) else sanitized_output
        
        return {
            **state,
            "messages": sanitized_message,
            "output": sanitized_message
        }

    
    # Create the workflow graph
    workflow = StateGraph(AgentState)
    
    # Add nodes for each step
    workflow.add_node("analyze_input", analyze_input)
    workflow.add_node("feature_unavailable", feature_unavailable)
    workflow.add_edge("feature_unavailable", END)
    workflow.add_node("route_to_agent", route_to_agent)
    workflow.add_node("CONVERSATION_AGENT", run_conversation_agent)
    workflow.add_node("RAG_AGENT", run_rag_agent)
    workflow.add_node("WEB_SEARCH_PROCESSOR_AGENT", run_web_search_processor_agent)
    workflow.add_node("BRAIN_TUMOR_AGENT", run_brain_tumor_agent)
    workflow.add_node("CHEST_XRAY_AGENT", run_chest_xray_agent)
    workflow.add_node("SKIN_LESION_AGENT", run_skin_lesion_agent)
    workflow.add_node("check_validation", handle_human_validation)
    workflow.add_node("human_validation", perform_human_validation)
    workflow.add_node("apply_guardrails", apply_output_guardrails)
    
    # Define the edges (workflow connections)
    workflow.add_node("ASSISTANT_INFO_AGENT", run_assistant_info_agent)
    workflow.add_edge("ASSISTANT_INFO_AGENT", END)
    workflow.add_node("TCM_HERB_AGENT", run_tcm_herb_agent)
    workflow.add_edge("TCM_HERB_AGENT", END)
    workflow.add_node('PHARMACOPOEIA_RESEARCH_AGENT', run_research_agent)
    workflow.add_edge('PHARMACOPOEIA_RESEARCH_AGENT', END)
    workflow.add_node('PUBMED_AGENT', run_pubmed_agent)
    workflow.add_edge('PUBMED_AGENT', END)
    workflow.set_entry_point("analyze_input")
    # workflow.add_edge("analyze_input", "route_to_agent")
    # Add conditional routing for guardrails bypass
    workflow.add_conditional_edges(
        "analyze_input",
        check_if_bypassing,
        {
            "feature_unavailable": "feature_unavailable",
            "apply_guardrails": "apply_guardrails",
            "route_to_agent": "route_to_agent",
            "ASSISTANT_INFO_AGENT": "ASSISTANT_INFO_AGENT",
            "TCM_HERB_AGENT": "TCM_HERB_AGENT",
            'PHARMACOPOEIA_RESEARCH_AGENT': 'PHARMACOPOEIA_RESEARCH_AGENT',
            'PUBMED_AGENT': 'PUBMED_AGENT'
        }
    )
    
    # Connect decision router to agents
    workflow.add_conditional_edges(
        "route_to_agent",
        lambda x: x["next"],
        {
            "feature_unavailable": "feature_unavailable",
            "CONVERSATION_AGENT": "CONVERSATION_AGENT",
            "RAG_AGENT": "RAG_AGENT",
            "WEB_SEARCH_PROCESSOR_AGENT": "WEB_SEARCH_PROCESSOR_AGENT",
            "BRAIN_TUMOR_AGENT": "BRAIN_TUMOR_AGENT",
            "CHEST_XRAY_AGENT": "CHEST_XRAY_AGENT",
            "SKIN_LESION_AGENT": "SKIN_LESION_AGENT",
            "needs_validation": "RAG_AGENT"  # Default to RAG if confidence is low
        }
    )
    
    # Connect agent outputs to validation check
    workflow.add_edge("CONVERSATION_AGENT", "check_validation")
    # workflow.add_edge("RAG_AGENT", "check_validation")
    workflow.add_edge("WEB_SEARCH_PROCESSOR_AGENT", "check_validation")
    workflow.add_conditional_edges("RAG_AGENT", confidence_based_routing)
    workflow.add_edge("BRAIN_TUMOR_AGENT", "check_validation")
    workflow.add_edge("CHEST_XRAY_AGENT", "check_validation")
    workflow.add_edge("SKIN_LESION_AGENT", "check_validation")

    workflow.add_edge("human_validation", "apply_guardrails")
    workflow.add_edge("apply_guardrails", END)
    
    workflow.add_conditional_edges(
        "check_validation",
        lambda x: x["next"],
        {
            "human_validation": "human_validation",
            END: "apply_guardrails"  # Route to guardrails instead of END
        }
    )
    
    # workflow.add_edge("human_validation", END)
    
    # Compile the graph
    return workflow.compile()


def init_agent_state() -> AgentState:
    """Initialize the agent state with default values."""
    return {
        "messages": [],
        "agent_name": None,
        "current_input": None,
        "has_image": False,
        "image_type": None,
        "output": None,
        "needs_human_validation": False,
        "retrieval_confidence": 0.0,
        "bypass_routing": False,
        "insufficient_info": False,
        "disabled_feature": None,
        "tcm_request": None,
        "tcm_result": None,
        "tcm_context": None,
        'research_request': None,
        'research_result': None,
        'research_context': None,
        'pubmed_request': None,
        'pubmed_result': None,
        'pubmed_context': None,
        "assistant_request": None,
        "assistant_result": None,
        "routing": None,
        "next": ""
    }


def _process_query(query: Union[str, Dict], thread_config: Dict) -> Dict:
    """
    Process a user query through the agent decision system.
    
    Args:
        query: User input (text string or dict with text and image)
        thread_config: Explicit session checkpoint configuration
        
    Returns:
        Response from the appropriate agent
    """
    # Initialize the graph
    graph = create_agent_graph()

    # # Save Graph Flowchart
    # image_bytes = graph.get_graph().draw_mermaid_png()
    # decoded = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), -1)
    # cv2.imwrite("./assets/graph.png", decoded)
    # print("Graph flowchart saved in assets.")
    
    # Read only the last committed turn, not partially completed graph execution.
    session_id = thread_config["configurable"]["thread_id"]
    snapshot = memory.load(session_id)
    state = init_agent_state()
    state['tcm_context'], state['research_context'] = split_contexts(snapshot.tcm_context)
    state['pubmed_context'] = pubmed_context_from(snapshot.tcm_context)
    
    # Add the current query
    state["current_input"] = query

    # To handle image upload case
    if isinstance(query, dict):
        query = query.get("text", "") + ", user uploaded an image for diagnosis."
    
    state["messages"] = snapshot.messages + [HumanMessage(content=query)]

    result = graph.invoke(state, thread_config)
    # print("######### DEBUG 4:", result)
    # state["messages"] = [result["messages"][-1].content]

    # Keep history to reasonable size (ANOTHER OPTION: summarize and store before truncating history)
    if len(result["messages"]) > config.max_conversation_history:  # Keep last config.max_conversation_history messages
        result["messages"] = result["messages"][-config.max_conversation_history:]
    memory.save(session_id, result["messages"], pack_contexts(result.get('tcm_context'), result.get('research_context'),
                result.get('pubmed_context')), revision=snapshot.revision)

    # visualize conversation history in console
    for m in result["messages"]:
        m.pretty_print()
    
    # Add the response to conversation history
    return result

def process_query(query: Union[str, Dict], conversation_history=None, *, session_id=None) -> Dict:
    """Run a turn in its own session; omitted IDs create isolated one-shot calls.

    Completed turns live in SQLite and survive restart. Client-supplied
    history is intentionally not trusted as server-side state.
    """
    with session_turn(session_id) as thread_config:
        return _process_query(query, thread_config)
