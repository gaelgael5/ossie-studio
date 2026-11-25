"""Ossie-studio, serveur d'autorisation OAuth 2.1 de sa propre surface MCP.

Pas de Keycloak ni d'IdP externe pour émettre les jetons (décision du
17 novembre 2026) : seul Ossie-studio connaît les profils agents et les
capacités des personnes, que l'écran de consentement doit montrer. La
connexion de la personne, elle, reste celle de l'IHM (compte local, OIDC).
"""
