%% nc.pl — Norm Check phase transition rules
%% Based on Chapter 5 of Jensen (2015)

:- dynamic current_scope/1.  % Bridge predicate for condition evaluation

%% ============================================================
%% Obligation rules
%% ============================================================

% Obl-Act: Activate obligation when condition holds and objective not achieved
% Guard: only evaluate ground conditions (non-ground handled at check time)
nc_activate_obligation :-
    forall(
        (   rea(Agent, Role),
            cond(Role, obliged, Obj, Deadline, Cond),
            ground(Cond),
            call(Cond),
            \+ achieved(Obj),
            \+ norm(Agent, Role, obliged, Obj, Deadline)
        ),
        assertz(norm(Agent, Role, obliged, Obj, Deadline))
    ).

% Obl-Sat: Fulfill obligation when objective is achieved
nc_fulfill_obligation :-
    forall(
        (   norm(Agent, Role, obliged, Obj, Deadline),
            achieved(Obj)
        ),
        retract(norm(Agent, Role, obliged, Obj, Deadline))
    ).

% Obl-Viol: Violate obligation when deadline reached and objective not achieved
nc_violate_obligation :-
    forall(
        (   norm(Agent, Role, obliged, Obj, Deadline),
            \+ achieved(Obj),
            deadline_reached(Deadline),
            \+ viol(Agent, Role, obliged, Obj)
        ),
        (   retract(norm(Agent, Role, obliged, Obj, Deadline)),
            assertz(viol(Agent, Role, obliged, Obj))
        )
    ).

%% ============================================================
%% Prohibition rules
%% ============================================================

% Pro-Act: Activate prohibition when condition holds
% Guard: only activate ground prohibitions (non-ground evaluated at check time)
nc_activate_prohibition :-
    forall(
        (   rea(Agent, Role),
            cond(Role, forbidden, Obj, Deadline, Cond),
            ground(Obj),
            ground(Cond),
            call(Cond),
            \+ norm(Agent, Role, forbidden, Obj, Deadline)
        ),
        assertz(norm(Agent, Role, forbidden, Obj, Deadline))
    ).

% Pro-Exp: Expire prohibition when deadline reached
nc_expire_prohibition :-
    forall(
        (   norm(Agent, Role, forbidden, Obj, Deadline),
            Deadline \== false,
            deadline_reached(Deadline)
        ),
        retract(norm(Agent, Role, forbidden, Obj, Deadline))
    ).

%% ============================================================
%% Permission check predicates
%% ============================================================

% Check if a specific action is permitted for agent in role
check_action_permitted(Agent, Role, Action) :-
    rea(Agent, Role),
    \+ check_action_blocked(Agent, Role, Action, _).

% Check via activated norms (ground prohibitions activated by NC phase)
check_action_blocked(Agent, Role, Action, BlockedObj) :-
    norm(Agent, Role, forbidden, BlockedObj, _),
    Action = BlockedObj.

% Check via conditional prohibitions (evaluated at check time)
% When Action unifies with Obj, shared variables in Cond get bound,
% allowing condition evaluation with concrete values.
check_action_blocked(Agent, Role, Action, BlockedObj) :-
    rea(Agent, Role),
    cond(Role, forbidden, BlockedObj, _Deadline, Cond),
    Action = BlockedObj,
    call(Cond).

%% ============================================================
%% NC phase driver
%% ============================================================

% Run the full NC phase for a given agent and role
nc_run(Agent, Role) :-
    (rea(Agent, Role) -> true ; true),
    nc_activate_obligation,
    nc_fulfill_obligation,
    nc_violate_obligation,
    nc_activate_prohibition,
    nc_expire_prohibition.
