%% og.pl — Option Generation phase
%% Based on Chapter 5 of Jensen (2015)

%% ============================================================
%% Option types:
%%   enact(Role)          - role enactment option
%%   deact(Role)          - role deactment option
%%   norm(Deon, Obj)      - active norm option
%%   violation(Deon, Obj) - violation option
%%   delegate(Role, Obj)  - delegation option
%%   inform(Role, Obj)    - inform option
%% ============================================================

% Enact: Role is an option if agent is not enacting it and it has capabilities
% role/2 must come first to bind Role before negation-as-failure check
og_option(Agent, enact(Role)) :-
    role(Role, _),
    \+ rea(Agent, Role),
    cap(Role, _).

% Deact: Role deactment is an option if all objectives fulfilled
og_option(Agent, deact(Role)) :-
    rea(Agent, Role),
    role(Role, Objectives),
    Objectives \== [],
    forall(member(Obj, Objectives), achieved(Obj)).

% Norm: Active norms become options
og_option(Agent, norm(Deon, Obj)) :-
    norm(Agent, _Role, Deon, Obj, _Deadline).

% Violation: Violations become options
og_option(Agent, violation(Deon, Obj)) :-
    viol(Agent, _Role, Deon, Obj).

% Delegate: Dependency relations generate delegation options
og_option(Agent, delegate(DepRole, Obj)) :-
    rea(Agent, Role),
    dep(Role, DepRole, Obj),
    \+ achieved(Obj).

% Inform: When an objective is achieved that another role depends on
og_option(Agent, inform(DepRole, Obj)) :-
    rea(Agent, Role),
    dep(DepRole, Role, Obj),
    achieved(Obj).
