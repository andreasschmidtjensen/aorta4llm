%% metamodel.pl — AORTA organizational metamodel predicates
%% Based on Definition 4.7 from Jensen (2015)

%% Dynamic predicates — asserted at runtime by the Python engine
:- dynamic role/2.          % role(Role, Objectives)
:- dynamic obj/2.           % obj(Objective, SubObjectives)
:- dynamic dep/3.           % dep(Role1, Role2, Objective)
:- dynamic cap/2.           % cap(Role, Capability)
:- dynamic cond/5.          % cond(Role, Deontic, Objective, Deadline, Condition)
:- dynamic rea/2.           % rea(Agent, Role)
:- dynamic norm/5.          % norm(Agent, Role, Deontic, Objective, Deadline)
:- dynamic viol/4.          % viol(Agent, Role, Deontic, Objective)
:- dynamic achieved/1.      % achieved(Objective)
:- dynamic deadline_reached/1. % deadline_reached(Deadline)

%% Utility predicates

% Agent has capability Cap through role R
has_capability(Agent, Cap) :-
    rea(Agent, R),
    cap(R, Cap).

% Action is prohibited for agent (active prohibition norm exists)
is_prohibited(Agent, Role, Action) :-
    norm(Agent, Role, forbidden, Action, _).

% Action is permitted if no active prohibition blocks it
% (permissions are derived, not stored — Section 4.1)
is_permitted(Agent, Role, Action) :-
    rea(Agent, Role),
    \+ is_prohibited(Agent, Role, Action).
