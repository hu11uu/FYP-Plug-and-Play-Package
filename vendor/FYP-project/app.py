import streamlit as st

if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "username" not in st.session_state:
    st.session_state.username = None

def logout_function():
    st.session_state.authenticated = False
    for key in list(st.session_state.keys()): 
        del st.session_state[key]
    st.rerun()

login_signup_page = st.Page("pages/login_signup_page.py", title = "Login/Signup", icon="🔑")
chatbot_main_page = st.Page("pages/main_page.py", title = "NTU Chatbot Helper", icon="🤖", default=True)
survey_gen_page = st.Page("pages/survey_generator_main_page.py", title = "Survey Generator", icon="🛠️")
survey_vis_page = st.Page("pages/survey_visualisation_page.py", title = "Survey Visualisation", icon="📊")
context_uploader_page = st.Page("pages/context_uploader_page.py", title = "Chatbot Customisation", icon="📝")
logout = st.Page(logout_function, title = "Logout", icon="🚪")

# Handle navigation based on authentication and user role
if st.session_state.authenticated:
    # Base navigation items for all authenticated users
    nav_items = {
        "Account": [logout],
        "Chat": [chatbot_main_page],
    }
    
    # Only add Tools section if the user is staff
    if st.session_state.username == "staff":
        nav_items["Tools"] = [survey_gen_page, context_uploader_page, survey_vis_page]
    
    # Create navigation with the appropriate items
    pg = st.navigation(nav_items)
else:
    # Navigation for unauthenticated users
    pg = st.navigation([login_signup_page])

# Run the selected page
pg.run()