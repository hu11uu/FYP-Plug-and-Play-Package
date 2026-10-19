from helper.response import *
from helper.init import *
from helper.conversation_display import *
from helper.survey import *
from helper.survey_gen import *
import time

st.set_page_config(page_title="NTU Teaching Assistant Chatbot", layout="wide")

if 'initialised' not in st.session_state:
    init() 
    st.session_state.initialised = True

main_chatbot_interface()
#st.session_state.conversation

    






