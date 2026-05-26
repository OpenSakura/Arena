export const enResource = {
  app: {
    title: "OpenSakura Arena",
    name: "OpenSakura Arena",
    tagline: "Judge Japanese to Chinese translation battles.",
  },
  language: {
    switcherLabel: "Language / 语言",
    current: "English",
    english: "English",
    chinese: "Chinese",
  },
  auth: {
    login: "Login",
    logout: "Logout",
    signedIn: "Signed in",
    bootstrap: {
      loading: "Loading authentication…",
      refresh: "Please refresh the page to try again.",
      errors: {
        publicConfig: "Failed to load public config ({{status}})",
        session: "Failed to load auth session ({{status}})",
        fallback: "Failed to load authentication session",
      },
    },
    errorRoute: {
      defaultMessage: "Authentication could not be completed. Please try again.",
    },
  },
  nav: {
    home: "Home",
    battle: "Battle",
    leaderboard: "Leaderboard",
    onboarding: "Profile",
    admin: "Admin",
  },
  header: {
    toggleMenu: "Toggle menu",
  },
  theme: {
    light: "Light",
    dark: "Dark",
    system: "System",
    toggle: "Toggle theme",
    switchToLight: "Switch to light theme",
    switchToDark: "Switch to dark theme",
  },
  common: {
    loading: "Loading...",
    retry: "Retry",
    save: "Save",
    cancel: "Cancel",
    close: "Close",
  },
  errors: {
    generic: "Something went wrong.",
    unauthorized: "Please sign in to continue.",
    sessionExpired: "Your session expired. Please sign in again.",
    boundary: {
      title: "Something went wrong",
      unexpected: "An unexpected error occurred",
      tryAgain: "Try again",
    },
  },
  routes: {
    home: "Home",
    battle: "Translation Battle",
    leaderboard: "Leaderboard",
    onboarding: "Profile",
    admin: "Admin",
    adminModels: "Models",
    adminTasks: "Tasks",
    adminServiceAccounts: "Service Accounts",
    notFound: "Page not found",
    authError: "Authentication error",
  },
  home: {
    title: "OpenSakura Arena",
    subtitle: "Compare translations and help rank models.",
    startBattle: "Start a Battle",
    viewLeaderboard: "View Leaderboard",
    taglineBadge: "Open-source translation arena",
    heroDescription: "Pairwise, blind comparisons of JP>ZH light-novel style translations. Vote on which output is better and help the community measure and improve translation models.",
    features: {
      blindTest: {
        title: "Blind A/B",
        description: "Two models translate the same text. You judge without knowing which is which."
      },
      communityVoting: {
        title: "Community Voting",
        description: "Vote on accuracy, fluency, style, and naturalness to build consensus."
      },
      rankings: {
        title: "Elo & BT Rankings",
        description: "Models are ranked using Elo and Bradley-Terry with 95% confidence intervals."
      }
    },
    howItWorks: {
      title: "How it Works",
      subtitle: "Four simple steps from source text to community-driven model rankings.",
      step1: {
        title: "Source Text",
        description: "A Japanese passage is selected from our curated task pool."
      },
      step2: {
        title: "Blind Translation",
        description: "Two models translate the same text. Identities are hidden."
      },
      step3: {
        title: "Cast Your Vote",
        description: "You read both outputs and pick the better translation."
      },
      step4: {
        title: "Rankings Update",
        description: "Models are re-ranked using Elo/BT after each vote."
      },
      stepLabel: "Step {{step}}"
    },
    stats: {
      ratingSystem: {
        label: "Rating System",
        value: "Elo + BT"
      },
      confidence: {
        label: "Confidence",
        value: "95% CI"
      },
      voting: {
        label: "Voting",
        value: "Blind A/B"
      },
      footer: "Join the community in evaluating JP→ZH translation quality. Every vote contributes to more accurate model rankings."
    }
  },
  footer: {
    tagline: "Open-source platform for blind pairwise evaluation of JP→ZH translation models. Powered by community votes.",
    navigation: "Navigation",
    project: "Project",
    builtWith: "Built with React, FastAPI & community passion",
    systems: "Elo + Bradley-Terry rating systems"
  },
  leaderboard: {
    title: "Leaderboard",
    empty: {
      title: "No ratings yet",
      description: "Start a battle and cast some votes to see models ranked here.",
      cta: "Start a battle"
    },
    rankLabel: "Rank {{rank}}",
    filters: {
      judgeType: {
        all: "All votes",
        human: "Human votes",
        bot: "Bot votes"
      },
      method: {
        elo: "Elo",
        bt: "Bradley-Terry"
      },
      confidence: {
        label: "95% CI",
        show: "Show 95% CI",
        hide: "Hide 95% CI"
      }
    },
    meta: {
      method: "Method: ",
      bootstrapRounds: " ({{count}} bootstrap rounds)",
      votes: {
        total: "Votes: ",
        totalCount: "<1>{{total}}</1> total",
        breakdown: " (<1>{{human}}</1> human, <2>{{bot}}</2> bot)"
      }
    },
    table: {
      rank: "Rank",
      model: "Model",
      rating: "Rating",
      confidence: "95% CI",
      games: "Games"
    },
    status: {
      unrated: "Unrated",
      new: "New"
    },
    podium: {
      rank: "#{{rank}} Rank",
      rating: "rating",
      games: "{{count}} games"
    },
    error: "Failed to load leaderboard"
  },
  battle: {
    title: "Translation Battle",
    sourceText: "Source text",
    chooseWinner: "Choose the better translation",
    submitVote: "Submit vote",
    modelOutput: "Model {{index}} output",
    comparisonAriaLabel: "Battle comparison",
    sourcePanelTitle: "Source Text",
    modelA: "Model A",
    modelB: "Model B",
    sessionExpiredTitle: "Session Expired",
    sessionExpiredBody: "Your session has expired. Please log in again.",
    voteHeader: "Cast Your Vote",
    votePrompt: "Which translation is better?",
    voteRecorded: "Vote recorded.",
    voteA: "Model A is better",
    voteTie: "Tie",
    voteB: "Model B is better",
    rubricPrompt: "Why did you choose this? (optional tags)",
    feedbackLabel: "Optional feedback",
    feedbackPlaceholder: "What influenced your decision?",
    submitVoteButton: "Submit Vote",
    updateVoteButton: "Update Vote",
    submittingVoteButton: "Submitting...",
    retryBattle: "Retry Battle",
    startAnother: "Start another battle",
    revealTitle: "Models Revealed",
    revealDescription: "Thank you for your vote! Here are the models behind each translation.",
    revealWinnerBadge: "Winner",
    revealTie: "You voted this as a tie",
    statusThinking: "Thinking...",
    statusStreaming: "Streaming",
    statusWaiting: "Waiting for output...",
    statusComplete: "Complete",
    statusReconnecting: "Reconnecting...",
    statusFailed: "Failed",
    statusError: "Error",
    statusLoading: "Loading...",
    adminRevealButton: "Reveal model",
    adminRevealAria: "Reveal {{title}} identity",
    rubric: {
      accuracy: "Accuracy",
      fluency: "Fluency",
      style: "Style",
      consistency: "Consistency",
      naturalness: "Naturalness"
    },
    errorEmptyStateTitle: "Unable to load battle",
    returnHome: "Return Home",
    errors: {
      loginRequiredToStart: "Login required to start a battle.",
      loginRequiredToView: "Login required to view battles.",
      loginRequiredToSubmit: "Login required to submit a vote.",
      loginRequiredToRetry: "Login required to retry.",
      permissionDenied: "Permission denied. You can only view your own battles.",
      failedToLoad: "Failed to load battle",
      streamFailed: "Battle stream failed",
      streamEndedEarly: "Battle stream ended before completion",
      sessionExpiredReload: "Session expired or authentication failed. Please reload the page.",
      failedToSubmitVote: "Failed to submit vote",
      failedToRetry: "Failed to retry battle",
      invalidBattleId: "Invalid battle ID",
      battleFailedPrefix: "Battle failed: ",
      battleFailedFallback: "Battle failed to complete",
      battleErrorPrefix: "Battle error: ",
      runErrorPrefixA: "Run error (A): ",
      runErrorPrefixB: "Run error (B): ",
      runErrorPrefixGen: "Run error: ",
      runErrorFallbackA: "Translation run A encountered an error",
      runErrorFallbackB: "Translation run B encountered an error",
      runErrorFallbackGen: "A translation run encountered an error",
      invalidBattleResponse: "Invalid battle response format",
      invalidVoteResponse: "Invalid vote response format"
    },
  },
  onboarding: {
    title: "Profile",
    description: "Add a little context about your language background. This helps with offline analysis and filtering.",
    authNotice: {
      expiredTitle: "Session expired",
      expiredBody: "Your session expired before we could load or save your profile. Sign in again to save profile info, create battles, view battles, and vote. You can still browse the leaderboard while signed out.",
      requiredTitle: "Login required to save",
      requiredBody: "You can browse the leaderboard without logging in, but creating battles, viewing battles, and voting require a login. Profile info is stored for logged-in users only."
    },
    checkingLogin: "Checking login...",
    identity: {
      title: "Identity",
      displayName: "Display name (optional)",
      displayNamePlaceholder: "e.g., N1 translator"
    },
    languagePrefs: {
      title: "Language Preferences",
      uiLanguage: "UI language",
      zhVariant: "Chinese variant",
      uiLangOptions: {
        en: "English",
        zh: "Chinese",
        ja: "Japanese"
      },
      zhVariantOptions: {
        "zh-Hans": "Simplified (zh-Hans)",
        "zh-Hant": "Traditional (zh-Hant)",
        unknown: "Unknown"
      }
    },
    experience: {
      title: "Experience",
      jlpt: "Japanese proficiency (self-reported)",
      years: "JP->ZH experience (years)",
      roles: "Roles",
      roleOptions: {
        translator: "translator",
        editor: "editor",
        qc: "qc",
        tl: "tl"
      }
    },
    consent: {
      title: "Consent",
      research: "Allow using my profile answers for offline filtering/research."
    },
    save: {
      button: "Save profile",
      saving: "Saving...",
      loading: "Loading...",
      success: "Saved successfully",
      loadError: "Failed to load profile",
      saveError: "Failed to save profile"
    }
  },
  admin: {
    title: "Admin",
    models: "Models",
    tasks: "Tasks",
    layout: {
      title: "Admin",
      tabs: {
        models: "Models",
        tasks: "Tasks",
        serviceAccounts: "Service Accounts"
      },
      guards: {
        notAuthorized: "You are not authorized to access the admin area.",
        sessionExpired: "Your session has expired. Please log in again."
      }
    },
    tasksRoute: {
      title: "Tasks & Task Sets",
      confirmDeleteTaskSet: "Delete this task set? (must be empty)",
      confirmDeleteTask: "Delete this task?",
      createSet: {
        title: "Create task set"
      },
      editSet: {
        title: "Edit selected task set"
      },
      createTask: {
        title: "Create single task"
      },
      editTask: {
        title: "Edit task"
      },
      import: {
        title: "Import tasks (.jsonl)",
        fileAriaLabel: "Select JSONL file to import"
      },
      taskSets: {
        title: "Task sets",
        allTasks: "All tasks"
      },
      tasks: {
        title: "Tasks",
        setLabel: "set: {{id}}",
        headers: {
          id: "id",
          lang: "lang",
          text: "text",
          actions: "actions"
        }
      },
      form: {
        name: "Name",
        namePlaceholder: "e.g., public_jp_ln_samples",
        description: "Description",
        optionalPlaceholder: "optional",
        metadataOptional: "Metadata (optional JSON object)",
        metadata: "Metadata (JSON object)",
        setMetadataPlaceholder: '{"license":"public","source":"curated"}',
        taskMetadataPlaceholder: '{"work":"...","chapter":"..."}',
        sourceLang: "Source language code",
        targetLang: "Target language code",
        defaultSourceLang: "Default source language code",
        defaultTargetLang: "Default target language code",
        sourceText: "Source text",
        sourceTextPlaceholder: "Japanese source text",
        taskSetId: "Task set ID"
      },
      actions: {
        create: "Create",
        creating: "Creating...",
        save: "Save",
        saving: "Saving...",
        delete: "Delete",
        edit: "Edit",
        close: "Close",
        import: "Import",
        importing: "Importing...",
        showMore: "Show more"
      },
      values: {
        none: "(none)"
      },
      status: {
        taskSet: "Task set: {{id}}",
        showingTasks: "Showing {{count}} task(s)",
        showingTasksForSet: "Showing {{count}} task(s) for task_set_id={{taskSetId}}",
        imported: "Imported {{count}} tasks from {{filename}}",
        showingVisible: "Showing {{visible}} of {{total}} tasks"
      },
      errors: {
        invalidTaskSetsResponse: "Invalid task sets response",
        invalidTasksResponse: "Invalid tasks response",
        invalidTaskSetResponse: "Invalid task set response",
        invalidTaskResponse: "Invalid task response",
        invalidImportResponse: "Invalid import response",
        loadTaskSetsFailed: "Failed to load task sets",
        loadTasksFailed: "Failed to load tasks",
        createTaskSetFailed: "Failed to create task set",
        updateTaskSetFailed: "Failed to update task set",
        deleteTaskSetFailed: "Failed to delete task set",
        createTaskFailed: "Failed to create task",
        updateTaskFailed: "Failed to update task",
        deleteTaskFailed: "Failed to delete task",
        importTasksFailed: "Failed to import tasks",
        nameRequired: "name is required",
        sourceTextRequired: "source_text is required",
        sourceLangRequired: "source_lang is required",
        targetLangRequired: "target_lang is required",
        selectJsonlFirst: "Select a .jsonl file first",
        invalidJsonSyntax: "Invalid JSON syntax",
        expectedJsonObject: "Expected a JSON object"
      }
    },
    modelRegistry: {
      title: "Model Registry",
      confirmDelete: "Delete this model?",
      testResult: "Test: {{status}} ({{note}})",
      create: {
        title: "Create model"
      },
      edit: {
        title: "Edit model"
      },
      form: {
        displayName: "Display name",
        displayNamePlaceholder: "e.g., gpt-4o-mini (gateway)",
        modelName: "Model name",
        modelNamePlaceholder: "e.g., gpt-4o-mini",
        baseUrl: "Base URL",
        baseUrlPlaceholder: "https://gateway.example.com (or .../v1)",
        apiKeyOptional: "API key (optional)",
        apiKeyPlaceholder: "stored encrypted at rest",
        temperature: "temperature",
        frequencyPenalty: "frequency_penalty",
        presencePenalty: "presence_penalty",
        optionalPlaceholder: "(optional)",
        systemPrompt: "system_prompt (leave blank for default)",
        systemPromptPlaceholder: "You are an expert translator...",
        userPrompt: "user_prompt (leave blank for default)",
        userPromptPlaceholder: "Translate the following from {{sourceLang}} to {{targetLang}}:\n{{sourceText}}",
        promptTokensPrefix: "Supported prompt tokens:",
        promptTokenSeparator: ",",
        promptTokenEnd: ".",
        promptTokensSuffix: " Leave both prompts blank to use the built-in defaults.",
        tagsJson: "tags (JSON object)",
        visibility: "visibility",
        enabled: "enabled",
        paramsJson: "params (JSON object)",
        apiKeyHelper: "Model API keys are never returned by the backend.",
        newApiKeyOptional: "New API key (optional)",
        newApiKeyPlaceholder: "leave blank to keep",
        clearApiKey: "Clear API key"
      },
      actions: {
        create: "Create",
        creating: "Creating...",
        edit: "Edit",
        test: "Test",
        delete: "Delete",
        close: "Close",
        save: "Save",
        saving: "Saving..."
      },
      list: {
        title: "Models",
        empty: "No models yet.",
        headers: {
          name: "Name",
          model: "Model",
          visibility: "Visibility",
          enabled: "Enabled",
          key: "Key",
          actions: "Actions"
        }
      },
      values: {
        yes: "yes",
        no: "no",
        ok: "ok",
        fail: "fail"
      },
      errors: {
        invalidModelsResponse: "Invalid models response",
        invalidModelResponse: "Invalid model response",
        invalidModelTestResponse: "Invalid model test response",
        loadFailed: "Failed to load models",
        createFailed: "Failed to create model",
        saveFailed: "Failed to save model",
        deleteFailed: "Failed to delete model",
        testFailed: "Failed to test model",
        displayNameRequired: "display_name is required",
        modelNameRequired: "model_name is required",
        baseUrlRequired: "base_url is required",
        invalidNumber: "Invalid number",
        invalidJsonSyntax: "Invalid JSON syntax",
        expectedJsonObject: "Expected a JSON object"
      }
    },
    serviceAccounts: {
      title: "Service Accounts",
      confirmRevoke: "Are you sure you want to revoke this token?",
      create: {
        title: "Create service account"
      },
      edit: {
        title: "Edit service account"
      },
      form: {
        name: "Name",
        namePlaceholder: "e.g., CI/CD Bot",
        description: "Description",
        descriptionOptional: "Description (optional)",
        enabled: "enabled"
      },
      list: {
        title: "Accounts",
        empty: "No service accounts found."
      },
      token: {
        createdTitle: "Token created",
        createdWarning: "Copy now. This token will not be shown again.",
        listTitle: "Tokens",
        createTitle: "Create Token",
        scopesLabel: "Scopes",
        expiresAtOptional: "Expires At (optional)",
        empty: "No tokens.",
        statusScopes: "{{status}} • scopes: {{scopes}}",
        expires: " • expires: {{date}}"
      },
      scopes: {
        battleCreate: {
          label: "Create battles",
          description: "Allows creating new translation battles."
        },
        battleRead: {
          label: "Read battles",
          description: "Allows reading battle state and results."
        },
        battleExecute: {
          label: "Execute battles",
          description: "Allows service workers to execute battle runs."
        },
        voteCreate: {
          label: "Submit votes",
          description: "Allows submitting votes for battles."
        }
      },
      actions: {
        create: "Create",
        creating: "Creating...",
        edit: "Edit",
        tokens: "Tokens",
        hideTokens: "Hide Tokens",
        newToken: "New Token",
        confirmCreate: "Confirm Create",
        cancel: "Cancel",
        dismiss: "Dismiss",
        revoke: "Revoke",
        revoking: "...",
        close: "Close",
        save: "Save",
        saving: "Saving..."
      },
      values: {
        disabled: "disabled"
      },
      errors: {
        loadFailed: "Failed to load accounts",
        createFailed: "Failed to create",
        saveFailed: "Failed to save",
        createTokenFailed: "Failed to create token",
        revokeTokenFailed: "Failed to revoke token",
        nameRequired: "name is required",
        scopeRequired: "at least one scope is required",
        invalidResponseFormat: "Invalid response format",
        invalidCreateResponse: "Invalid create response",
        invalidUpdateResponse: "Invalid update response",
        invalidCreateTokenResponse: "Invalid create token response",
        invalidRevokeResponse: "Invalid revoke response"
      }
    }
  },
} as const;
