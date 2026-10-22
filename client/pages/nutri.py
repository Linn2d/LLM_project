import streamlit as st
import os
import time
from server.mistral.mistralapi import MistralAPI
from server.security.prompt_guard import Guardrail
import asyncio
from typing import List, Dict
from datetime import datetime
from server.db.dbmanager import (
    load_conversations,
    load_messages,
    update_conversation,
    create_conversation,
    save_message,
    get_conversation_title,
    update_conversation_title,
    delete_conversation,
    load_chatbot_suggestions,
    save_chatbot_suggestions,
    save_recipes_with_ingredients,
    add_ingredients_column_if_not_exists
)

import logging


logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler()])
logger = logging.getLogger(__name__)

# 🔹 Chargement des variables de session pour éviter les rechargements inutiles
if "id_conversation" not in st.session_state:
    st.session_state.id_conversation = None

if "mistral_model" not in st.session_state:
    st.session_state["mistral_model"] = "ministral-3b-2512"

if "messages" not in st.session_state:
    st.session_state.messages = []  # Initialise l'historique des messages


# 🔹 Initialisation unique de MistralAPI
if "mistral_instance" not in st.session_state:
    print("🔄 Initialisation de MistralAPI...")
    st.session_state.mistral_instance = MistralAPI(
        model=st.session_state["mistral_model"]
    )
    print("✅ MistralAPI initialisé avec succès.")

mistral = st.session_state.mistral_instance  # Récupérer l'instance stockée

# 🔹 Initialisation de la sécurité (Guardrail)
try:
    guardrail = Guardrail()
except Exception as e:
    st.error(
        f"❌ Guardrail introuvable. Veuillez relancer l'appli ou contacter l'équipe de développement. Détails : {e}"
    )
    st.stop()

# 🔹 Chargement de la base de données
db_manager = st.session_state["db_manager"]
user_id = st.session_state["user_id"]
add_ingredients_column_if_not_exists(db_manager)
if "chatbot_suggestions" not in st.session_state:
    st.session_state["chatbot_suggestions"] = load_chatbot_suggestions(
        db_manager, user_id
    )

# 🔹 Sidebar : Bouton "➕ Nouveau chat" en haut
st.sidebar.title("🗂️ Historique")
if st.sidebar.button("➕ Nouveau chat"):
    title = "Nouvelle conversation"
    new_conversation_id = create_conversation(db_manager, title, user_id)
    st.session_state.id_conversation = new_conversation_id
    st.session_state.messages = []
    st.rerun()

# 🔹 Charger l'historique des conversations
conversation_history = load_conversations(db_manager, user_id) or []

# 🔹 Sidebar : Affichage de l'historique des conversations avec bouton de suppression
for index, conversation in enumerate(conversation_history):
    id_conversation = conversation["id_conversation"]
    title = conversation["title"]
    key = f"conversation_{id_conversation}_{index}"  # Clé unique

    col1, col2 = st.sidebar.columns(
        [0.8, 0.2]
    )  # 🔹 Disposition pour aligner le bouton de suppression

    with col1:
        if (
            "id_conversation" in st.session_state
            and st.session_state.id_conversation == id_conversation
        ):
            st.button(f"🟢 {title}", key=key, disabled=True)
        else:
            if st.button(title, key=key):
                st.session_state.id_conversation = id_conversation
                st.session_state.messages = load_messages(db_manager, id_conversation)
                update_conversation(
                    db_manager,
                    id_conversation=st.session_state.id_conversation,
                    id_utilisateur=user_id,
                )
                st.rerun()

    with col2:
        if st.button("🗑️", key=f"delete_{id_conversation}"):  # 🔥 Bouton de suppression
            delete_conversation(db_manager, id_conversation)
            st.rerun()  # Rafraîchir après suppression
# 🔹 Titre centré (zone principale)
if not st.session_state.messages:
  st.markdown(
      """
      <h1 style='text-align: center;'>👋 Bonjour, que puis-je faire pour vous ?</h1>
      <p style='text-align: center; color: gray;'>Posez-moi une question sur vos recettes ou vos repas.</p>
      """,
      unsafe_allow_html=True,
  )
# 🔹 Affichage des messages précédents dans l'interface
for message in st.session_state.messages:
    timestamp = message["timestamp"]
    latency = message["temps_traitement"]
    latency_text = f"⏳ {latency} sec" if latency is not None else ""

    if message["role"] == "user":
        with st.chat_message("user"):
            st.markdown(message["content"])
            st.caption(f"📅 {timestamp} {latency_text}")

    elif message["role"] == "assistant":
        with st.chat_message("assistant", avatar="client/assets/avatar_bot_big.jpg"):
            st.markdown(message["content"])
            st.caption(
                f"📅 {timestamp} {latency_text}"
            )  # 🔹 Ajout de l'heure et de la latence


# 🔹 Interface utilisateur - Zone d'entrée utilisateur
if prompt := st.chat_input("Dîtes quelque-chose"):

    # Calcul du nombre de tokens avant d'envoyer le prompt
    input_tokens = mistral.count_input_tokens([{"content": prompt}])
    print(f"✅ Nombre de tokens en entrée : {input_tokens}")

    # 🔸 Vérifier si le message est dans une langue supportée par le guardrail
    is_supported = guardrail.analyze_language(prompt)
    if not is_supported:
        st.warning(
            """
                   ⚠️ Votre message n'est pas rédigé dans les langues actuellement supportées (FR, EN, DE, ES).   
                   Si votre message est pourtant dans une des langues supportées, le reformuler ou l'allonger peut être utile.
                   """
        )
        st.stop()

    # 🔸 Vérifier la sécurité du message
    is_safe = guardrail.analyze_query(prompt)

    # 🔸 Afficher immédiatement le message de l'utilisateur
    with st.chat_message("user"):
        st.markdown(prompt)

    if st.session_state.id_conversation is None:
        # Générer un titre basé sur le premier message
        title = mistral.auto_wrap(text=prompt, temperature=0.5)
        st.session_state.id_conversation = create_conversation(
            db_manager, title, user_id
        )
    else:
        # Vérifier si le titre est encore "Nouvelle conversation" et le mettre à jour si nécessaire
        current_title = get_conversation_title(
            db_manager, st.session_state.id_conversation
        )
        if current_title == "Nouvelle conversation":
            new_title = mistral.auto_wrap(text=prompt, temperature=0.5)
            update_conversation_title(
                db_manager, st.session_state.id_conversation, new_title
            )

    # 🔸 Ajouter le message à l'historique et l'enregistrer dans la base de données
    st.session_state.messages.append(
        {
            "role": "user",
            "content": prompt,
            "temps_traitement": None,
            "total_cout": None,
            "impact_eco": None,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    )
    save_message(
        db_manager,
        st.session_state.id_conversation,
        role="user",
        content=prompt,
        temps_traitement=None,
        total_cout=None,
        impact_eco=None,
    )

    # 🔸 Si le message est interdit, afficher l'alerte mais NE PAS l'envoyer à Mistral
    if not is_safe:
        st.warning("⚠️ Votre message ne respecte pas l'usage de notre chatbot.")
        st.stop()  # Arrêter l'exécution ici pour ne PAS envoyer à Mistral

    retries = 0
    max_retries = 3

    # 🔹 Générer une réponse avec Mistral et RAG
    with st.chat_message("assistant", avatar="client/assets/avatar_bot_big.jpg"):
        retries = 0
        max_retries = 3
        while retries < max_retries:
            response = ""
            response_placeholder = st.empty()
            try:
                start_time = time.time()  # 🔹 Début du chronomètre

                print("🔄 Génération de réponse en cours...")
                stream_response = mistral.stream(
                    st.session_state.messages, temperature=0.5
                )

                # Compteur pour les tokens de sortie
                # Comment devenir une meilleure personne dans la vie ?
                output_tokens = 0
                temp_stream = [
                    chunk.data.choices[0].delta.content for chunk in stream_response if chunk.data.choices[0].delta.content
                ]
                # Calculer les tokens pour ce morceau de réponse
                temp_output_token = sum(
                    [mistral.count_output_tokens(chunk) for chunk in temp_stream]
                )
                
                reponse = " ".join(temp_stream)

                if response == "Injection":
                    st.warning(
                        "⚠️ Votre message ne respecte pas l'usage de notre chatbot."
                    )
                    # guardrail.incremental_learning(prompt, 1) # 1 car injection. Le tuning ne se fait que sur les injections
                    st.stop()
                    # end_time = time.time()  # 🔹 Fin du chronomètre
                    break
                else:  # on réinitialise
                    response = ""
                    for chunk in temp_stream:
                        response += chunk
                        response_placeholder.markdown(response)
                        # # Calculer les tokens pour ce morceau de réponse
                        # output_tokens += mistral.count_output_tokens(chunk.data.choices[0].delta.content)

                        time.sleep(0.03)

                # 🔹 Vérifier si la réponse contient des suggestions de recettes
                keywords = ["recette", "plat", "préparer", "ingrédients"]

                for word in keywords:
                    if word in response.lower():
                        try:
                            # 🔹 Extraire plusieurs titres de recettes
                            suggested_recipes = mistral.extract_multiple_recipes(
                                text=response, temperature=0.3
                            )

                            # Vérifier et initialiser la liste des suggestions
                            if "chatbot_suggestions" not in st.session_state:
                                st.session_state["chatbot_suggestions"] = []

                            # Ajouter uniquement les recettes qui ne sont pas déjà stockées
                            new_recipes = [
                                recipe
                                for recipe in suggested_recipes
                                if recipe not in st.session_state["chatbot_suggestions"]
                            ]

                            if new_recipes:
                                st.session_state["chatbot_suggestions"].extend(
                                    new_recipes
                                )  # Ajouter plusieurs recettes
                                print(
                                    f"✅ {len(new_recipes)} nouvelles suggestions ajoutées."
                                )

                                # 🔹 Sauvegarder les suggestions dans la BDD
                                save_chatbot_suggestions(
                                    db_manager, user_id, new_recipes
                                )

                        except Exception as e:
                            print(
                                f"❌ Erreur lors de l'extraction des suggestions : {e}"
                            )

                        break  # On ne veut ajouter qu'une seule suggestion par réponse
                time.sleep(1)   
                # 🔹 EXTRACTION ET ENREGISTREMENT DES RECETTES
                # 🔍 Vérifier si la réponse contient des suggestions de recettes
                # 🔹 EXTRACTION ET ENREGISTREMENT DES RECETTES
                # 🔍 Vérifier si la réponse contient des suggestions de recettes
                for word in keywords:
                    if word in response.lower():
                        try:
                            print("🔍 Détection de recettes, extraction en cours...")

                            # 🔹 Utiliser `extract_recipes_and_ingredients` pour extraire plusieurs recettes
                            extracted_recipes = mistral.extract_recipes_and_ingredients(response)

                            if extracted_recipes:
                                print(f"✅ {len(extracted_recipes)} recettes détectées : {extracted_recipes}")

                                # 🔹 Sauvegarder les recettes et leurs ingrédients dans la base de données
                                for recipe in extracted_recipes:
                                    title = recipe["titre"].lstrip("-").strip()  # Supprime les tirets et espaces inutiles
                                    ingredients = recipe["ingredients"].strip()

                                    # 🔹 Supprimer une virgule finale si présente dans le titre
                                    title_cleaned = title.rstrip(",")  # Enlève uniquement la virgule à la fin

                                    print(f"💾 Enregistrement de la recette '{title_cleaned}' avec les ingrédients : {ingredients}")

                                    save_recipes_with_ingredients(db_manager, user_id, title_cleaned, ingredients)

                            else:
                                print("⚠️ Aucune recette détectée.")

                        except Exception as e:
                            print(f"❌ Erreur lors de l'extraction des recettes et ingrédients : {e}")

                        break  # Éviter d'ajouter plusieurs fois la même recette


                end_time = time.time()  # 🔹 Fin du chronomètre
                latency = round(end_time - start_time, 2)  # 🔹 Calcul de la latence

                print(f"✅ Réponse générée en {latency} secondes.")
                print(f"✅ Nombre de tokens de sortie : {output_tokens}")
            except Exception as e:
                if hasattr(e, "status_code") and e.status_code == 429:
                    retries += 1
                    wait_time = 2 ** retries
                    stream_response = None
                    print(
                        f"⚠️ Rate limit atteint. Nouvel essai dans {wait_time} secondes..."
                    )
                    time.sleep(wait_time)
                else:
                    st.error(
                        f"❌ Erreur : Impossible de traiter votre demande. Détails : {str(e)}"
                    )
                    response_placeholder.markdown(
                        "❌ Erreur lors de la génération de la réponse."
                    )
                    st.stop()

            if response != "":  # On sort de la boucle
                end_time = time.time()  # 🔹 Fin du chronomètre
                latency = round(end_time - start_time, 2)  # 🔹 Calcul de la latence

                print(f"✅ Réponse générée en {latency} secondes.")
                print(f"✅ Nombre de tokens de sortie : {output_tokens}")
                break

        if retries >= max_retries:
            st.error("❌ Impossible d'obtenir une réponse après plusieurs tentatives.")
            response = "❌ Erreur : Limite de requêtes atteinte."

        # 🔹 Calcul du coût des tokens d'entrée et de sortie
        input_cost_per_token = 0.0004  # Coût par token d'entrée
        output_cost_per_token = 0.0005  # Coût par token de sortie

        # Calcul du coût des tokens d'entrée et de sortie
        input_cost = input_tokens * input_cost_per_token
        output_cost = output_tokens * output_cost_per_token

        # Calcul du coût total
        total_cost = input_cost + output_cost
        print(f"✅ Coût total de la requête : {total_cost} USD")

        # Facteur d'émission (en grammes de CO₂ par token)
        EMISSIONS_PER_TOKEN = 0.00005  # estimation

        # Calcul de l'empreinte carbone pour les tokens d'entrée et de sortie
        input_emissions = input_tokens * EMISSIONS_PER_TOKEN
        output_emissions = output_tokens * EMISSIONS_PER_TOKEN
        total_emissions = input_emissions + output_emissions
        print(f"🌍 Impact écologique total de la requête : {total_emissions:.4f} g CO₂")

        # 🔹 Enregistrer la réponse de l'assistant
        st.session_state.messages.append(
            {
                "role": "assistant",
                "content": response,
                "temps_traitement": latency,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
        save_message(
            db_manager,
            st.session_state.id_conversation,
            role="assistant",
            content=response,
            temps_traitement=latency,
            total_cout=total_cost,
            impact_eco=total_emissions,
        )

    if response == "Injection":
        guardrail.incremental_learning(
            prompt, [1]
        )  # 1 car injection. Le tuning ne se fait que sur les injections
        print(
            "🤖 Entraînement du guardrail à reconnaître le prompt comme dangereux effectué avec succès"
        )
        st.stop()
# def check_if_ingredients_saved(db_manager, user_id):
#     query = "SELECT repas_suggestion, ingredients FROM suggestions_repas WHERE id_utilisateur = ?"
#     recipes = db_manager.execute_safe(query, (user_id,), fetch=True)

#     if recipes:
#         print("✅ Recettes enregistrées en base :")
#         for recipe in recipes:
#             recette_nom = recipe[0]
#             ingredients = recipe[1] if recipe[1] else "⚠️ Aucun ingrédient enregistré"
#             print(f"📌 {recette_nom} - Ingrédients : {ingredients}")
#     else:
#         print("⚠️ Aucune recette enregistrée.")

# # Appel pour tester
# check_if_ingredients_saved(db_manager, user_id)

